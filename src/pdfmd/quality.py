from __future__ import annotations

import ast
import html
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdfmd.code_analysis import (
    infer_code_kind,
    infer_code_language,
    normalize_code_language,
    resolve_code_language,
)
from pdfmd.models import (
    BlockType,
    IssueSeverity,
    PageQuality,
    ParsedDocument,
    PdfInspection,
    QualityIssue,
    QualityReport,
)
from pdfmd.table_utils import is_valid_gfm_table

TEXT_BLOCKS = {
    BlockType.TITLE,
    BlockType.HEADING,
    BlockType.PARAGRAPH,
    BlockType.LIST_ITEM,
    BlockType.TABLE,
    BlockType.FORMULA,
    BlockType.CODE,
    BlockType.CAPTION,
    BlockType.FOOTNOTE,
    BlockType.RAW_MARKDOWN,
}

_TRAILING_NUMBER_RUN = re.compile(r"(?<!\S)(\d+(?:[ \t]+\d+){3,})\s*$")
_ORPHAN_CODE_FRAGMENT = re.compile(r"[~'\"`()\[\]{}.,;:]+")
_CODE_STATEMENT_SIGNAL = re.compile(
    r"\b(?:class|def|elif|else|except|for|from|if|import|print|return|try|while|with)\b"
    r"|(?<![=!<>])=(?!=)"
)
_NUMBERED_HEADING = re.compile(r"^\s*\d+(?:\.(\d+))+")
_FENCE_START = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})(?P<info>[^`]*)$")
_PYTHON_SOURCE_START = re.compile(
    r"(?m)^\s*(?:@[\w.]+|async\s+def\b|class\b|def\b|from\s+\S+\s+import\b|import\b)"
)
_PYTHON_SOURCE_SIGNAL = re.compile(
    r"\b(?:async|await|class|def|elif|except|finally|for|from|if|import|lambda|raise|"
    r"print|return|try|while|with|yield)\b|^\s*[A-Za-z_]\w*\s*=",
    flags=re.MULTILINE,
)
_CONSOLE_OUTPUT_SIGNAL = re.compile(
    r"(?m)^\s*(?:Traceback\b|<class\s+['\"]|(?:[\w.]+\([^)]*\)|[A-Za-z_]\w*)"
    r"(?:\s+[A-Za-z_]\w*=(?:'[^']*'|\"[^\"]*\"|[-+]?\d+(?:\.\d+)?)){2,})"
)


@dataclass(slots=True)
class _LogicalCode:
    language: str
    pages: list[int] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    metadata: list[dict[str, Any]] = field(default_factory=list)
    raw_markdown: bool = False

    @property
    def text(self) -> str:
        return "\n".join(part.rstrip("\n\r") for part in self.parts)

    @property
    def first_page(self) -> int:
        return self.pages[0]

    @property
    def first_block_id(self) -> str | None:
        return self.block_ids[0] if self.block_ids else None

    def page_for_line(self, line_number: int | None) -> int:
        if line_number is None:
            return self.first_page
        consumed = 0
        for page, part in zip(self.pages, self.parts, strict=False):
            consumed += max(1, part.count("\n") + 1)
            if line_number <= consumed:
                return page
        return self.pages[-1]


@dataclass(frozen=True, slots=True)
class _CodeFinding:
    issue: QualityIssue
    deduction: float
    is_error: bool


def evaluate_quality(
    document: ParsedDocument,
    inspection: PdfInspection,
    output_dir: Path,
    *,
    minimum_score: float,
    primary_engine: str,
) -> QualityReport:
    issues: list[QualityIssue] = []
    page_reports: list[PageQuality] = []
    inspection_by_page = {page.number: page for page in inspection.pages}
    document_by_page = {page.number: page for page in document.pages}
    code_findings_by_page, code_metrics = _analyze_code_quality(document)
    flat_heading_pages, flat_heading_count = _flat_heading_findings(document)
    if flat_heading_pages:
        issues.append(
            QualityIssue(
                code="flat_heading_hierarchy",
                severity=IssueSeverity.ERROR,
                message=(
                    f"文档中 {flat_heading_count} 个编号标题的层级与编号深度不一致，"
                    "标题结构疑似被扁平化"
                ),
            )
        )

    for page_number in range(1, inspection.page_count + 1):
        source = inspection_by_page[page_number]
        parsed = document_by_page.get(page_number)
        score = 100.0
        page_text = ""
        duplicate_ratio = 0.0
        page_has_error = page_number in flat_heading_pages
        structured_table_present = False
        aligned_image_code_recovery = False

        if parsed is None:
            score = 0
            issues.append(
                QualityIssue(
                    code="missing_page",
                    severity=IssueSeverity.ERROR,
                    page=page_number,
                    message="解析结果缺少该页面",
                )
            )
        else:
            text_parts = [
                _quality_text(block) for block in parsed.blocks if block.type in TEXT_BLOCKS
            ]
            page_text = "\n".join(text_parts).strip()
            normalized_blocks = [
                _normalize(_quality_text(block))
                for block in parsed.blocks
                if block.type in TEXT_BLOCKS and _normalize(_quality_text(block))
            ]
            if normalized_blocks:
                counts = Counter(normalized_blocks)
                duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
                duplicate_ratio = duplicate_count / len(normalized_blocks)
                if duplicate_ratio >= 0.2:
                    score -= 15
                    issues.append(
                        QualityIssue(
                            code="duplicate_content",
                            severity=IssueSeverity.WARNING,
                            page=page_number,
                            message="页面包含较多重复文本块",
                        )
                    )

            code_blocks = [block for block in parsed.blocks if block.type is BlockType.CODE]
            image_code_blocks = [
                block for block in code_blocks if block.metadata.get("image_layout_recovered")
            ]
            aligned_image_code_recovery = any(
                _is_aligned_image_code_recovery(block) for block in image_code_blocks
            )
            if image_code_blocks:
                # High-confidence local OCR is a review signal, not proof of an
                # error.  Keep the warning visible without depressing the score
                # when visual alignment is strong; uncertain OCR retains a small
                # penalty.  Syntax failures are handled for all logical code
                # groups below rather than only for image-derived blocks.
                if not aligned_image_code_recovery:
                    score -= 5
                issues.append(
                    QualityIssue(
                        code="image_ocr_review_required",
                        severity=IssueSeverity.WARNING,
                        page=page_number,
                        message=(
                            "图片代码已按视觉行恢复，但 OCR 对齐不能证明字符完全正确，"
                            "请对照原图抽样核对"
                        ),
                    )
                )

            page_code_findings = code_findings_by_page.get(page_number, [])
            if page_code_findings:
                score -= min(45, sum(finding.deduction for finding in page_code_findings))
                page_has_error = page_has_error or any(
                    finding.is_error for finding in page_code_findings
                )
                issues.extend(finding.issue for finding in page_code_findings)
            collapsed_count = sum(_is_collapsed_code(block) for block in code_blocks)
            contaminated_count = sum(
                _has_trailing_line_numbers(block.text) for block in code_blocks
            )
            orphan_count = sum(_is_orphan_code_fragment(block.text) for block in code_blocks)
            if collapsed_count:
                score -= min(45, 30 + 5 * (collapsed_count - 1))
                page_has_error = True
                issues.append(
                    QualityIssue(
                        code="collapsed_code_layout",
                        severity=IssueSeverity.ERROR,
                        page=page_number,
                        message=f"检测到 {collapsed_count} 个疑似丢失换行的长代码块",
                    )
                )
            if contaminated_count:
                score -= min(45, 30 + 5 * (contaminated_count - 1))
                page_has_error = True
                issues.append(
                    QualityIssue(
                        code="line_number_contamination",
                        severity=IssueSeverity.ERROR,
                        page=page_number,
                        message=f"检测到 {contaminated_count} 个混入连续行号的代码块",
                    )
                )
            if orphan_count:
                orphan_is_error = orphan_count >= 2
                score -= 25 if orphan_is_error else 10
                page_has_error = page_has_error or orphan_is_error
                issues.append(
                    QualityIssue(
                        code="orphan_code_fragment",
                        severity=(
                            IssueSeverity.ERROR if orphan_is_error else IssueSeverity.WARNING
                        ),
                        page=page_number,
                        message=f"检测到 {orphan_count} 个脱离上下文的短代码片段",
                    )
                )

            tiny_assets = 0
            table_mismatches = 0

            for block in parsed.blocks:
                if block.type is BlockType.IMAGE and block.asset_path:
                    asset = (output_dir / block.asset_path).resolve()
                    try:
                        asset.relative_to(output_dir.resolve())
                    except ValueError:
                        asset = output_dir / "__invalid_asset__"
                    if not asset.is_file():
                        score -= 10
                        issues.append(
                            QualityIssue(
                                code="missing_asset",
                                severity=IssueSeverity.WARNING,
                                page=page_number,
                                block_id=block.id,
                                message=f"图片文件不存在：{block.asset_path}",
                            )
                        )
                    elif _is_tiny_asset(block, asset):
                        tiny_assets += 1
                if block.type is BlockType.TABLE and not (block.text or block.table_html):
                    score -= 15
                    issues.append(
                        QualityIssue(
                            code="empty_table",
                            severity=IssueSeverity.WARNING,
                            page=page_number,
                            block_id=block.id,
                            message="检测到空表格",
                        )
                    )
                elif block.type is BlockType.TABLE and _table_structure_mismatch(block):
                    table_mismatches += 1
                elif block.type is BlockType.TABLE and _has_structured_table_metadata(block):
                    structured_table_present = True

            if tiny_assets:
                score -= min(15, 5 * tiny_assets)
                issues.append(
                    QualityIssue(
                        code="tiny_asset",
                        severity=IssueSeverity.WARNING,
                        page=page_number,
                        message=f"检测到 {tiny_assets} 个疑似装饰或重复内联内容的微小图片",
                    )
                )
            if table_mismatches:
                score -= min(40, 30 + 5 * (table_mismatches - 1))
                page_has_error = True
                issues.append(
                    QualityIssue(
                        code="table_structure_mismatch",
                        severity=IssueSeverity.ERROR,
                        page=page_number,
                        message=f"检测到 {table_mismatches} 个表格的结构元数据与输出不一致",
                    )
                )

            if page_number in flat_heading_pages:
                score -= 25

        compact_text = "".join(page_text.split())
        extracted_chars = len(compact_text)
        replacement_ratio = compact_text.count("\ufffd") / max(1, extracted_chars)
        control_count = len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", page_text))
        control_ratio = control_count / max(1, extracted_chars)

        if not source.is_blank and extracted_chars == 0:
            score -= 70
            issues.append(
                QualityIssue(
                    code="empty_output",
                    severity=IssueSeverity.ERROR,
                    page=page_number,
                    message="非空页面没有提取到文本",
                )
            )
        elif source.native_text_chars >= 50:
            coverage = extracted_chars / max(1, source.native_text_chars)
            if coverage < 0.35:
                score -= 30
                issues.append(
                    QualityIssue(
                        code="low_text_coverage",
                        severity=IssueSeverity.ERROR,
                        page=page_number,
                        message=f"文本覆盖率过低（{coverage:.0%}）",
                    )
                )
            elif coverage < 0.65:
                score -= 12
                issues.append(
                    QualityIssue(
                        code="reduced_text_coverage",
                        severity=IssueSeverity.WARNING,
                        page=page_number,
                        message=f"文本覆盖率偏低（{coverage:.0%}）",
                    )
                )
            elif coverage > 1.6 and aligned_image_code_recovery:
                issues.append(
                    QualityIssue(
                        code="recovered_image_text_expansion",
                        severity=IssueSeverity.INFO,
                        page=page_number,
                        message=(
                            f"嵌入图片代码恢复使文本量达到原生文本的 {coverage:.0%}，"
                            "已通过高相似度对齐，并保留人工复核提示"
                        ),
                    )
                )
            elif coverage > 2.0:
                # PDF text layers often omit or heavily fragment table cells.  A
                # structurally consistent table can therefore legitimately contain
                # much more text than pypdf sees.  Keep it visible as a warning, but
                # reserve an error/fallback for unexplained expansion or pages that
                # already show structural corruption.
                table_explains_expansion = (
                    structured_table_present and not page_has_error and duplicate_ratio < 0.2
                )
                if table_explains_expansion:
                    score -= 15
                    issues.append(
                        QualityIssue(
                            code="excess_text_coverage",
                            severity=IssueSeverity.WARNING,
                            page=page_number,
                            message=(
                                f"表格页提取文本多于原生文本（{coverage:.0%}），"
                                "表格结构一致，建议抽样核对"
                            ),
                        )
                    )
                else:
                    score -= 30
                    page_has_error = True
                    issues.append(
                        QualityIssue(
                            code="excess_text_coverage",
                            severity=IssueSeverity.ERROR,
                            page=page_number,
                            message=(
                                f"提取文本显著多于原生文本（{coverage:.0%}），疑似重复或混入行号"
                            ),
                        )
                    )
            elif coverage > 1.6:
                score -= 15
                issues.append(
                    QualityIssue(
                        code="excess_text_coverage",
                        severity=IssueSeverity.WARNING,
                        page=page_number,
                        message=f"提取文本多于原生文本（{coverage:.0%}），建议检查结构完整性",
                    )
                )

        if replacement_ratio > 0.01:
            score -= min(35, replacement_ratio * 200)
            issues.append(
                QualityIssue(
                    code="garbled_text",
                    severity=IssueSeverity.ERROR,
                    page=page_number,
                    message=f"乱码字符比例过高（{replacement_ratio:.1%}）",
                )
            )
        if control_ratio > 0.005:
            score -= min(20, control_ratio * 200)
            issues.append(
                QualityIssue(
                    code="control_characters",
                    severity=IssueSeverity.WARNING,
                    page=page_number,
                    message="文本包含异常控制字符",
                )
            )

        score = max(0.0, min(100.0, score))
        page_reports.append(
            PageQuality(
                page=page_number,
                score=round(score, 2),
                extracted_chars=extracted_chars,
                replacement_ratio=round(replacement_ratio, 5),
                duplicate_ratio=round(duplicate_ratio, 5),
                needs_fallback=(score < minimum_score or page_has_error) and not source.is_blank,
            )
        )

    if primary_engine == "native":
        for page in page_reports:
            page.score = min(page.score, 88.0)
            page.needs_fallback = page.needs_fallback or (
                page.score < minimum_score and not inspection_by_page[page.page].is_blank
            )
        issues.append(
            QualityIssue(
                code="limited_layout_validation",
                severity=IssueSeverity.INFO,
                message="Native 引擎未执行版面和表格结构识别，质量分已按能力上限校准",
            )
        )
    mean_score = sum(page.score for page in page_reports) / max(1, len(page_reports))
    low_count = max(1, math.ceil(len(page_reports) * 0.1))
    low_score = (
        sum(sorted(page.score for page in page_reports)[:low_count]) / low_count
        if page_reports
        else 0.0
    )
    # A plain mean hides a badly damaged page inside a long document.  Blend in
    # the lowest decile, mirroring Docling's guidance to inspect both mean and
    # low confidence grades.
    overall = mean_score * 0.8 + low_score * 0.2
    fallback_pages = [page.page for page in page_reports if page.needs_fallback]
    metrics = _document_metrics(document)
    metrics.update(code_metrics)
    metrics.update(_docling_confidence_metrics(document))
    metrics.update(
        {
            "page_count": inspection.page_count,
            "failed_pages": len(fallback_pages),
            "issue_count": len(issues),
            "error_count": sum(issue.severity is IssueSeverity.ERROR for issue in issues),
            "warning_count": sum(issue.severity is IssueSeverity.WARNING for issue in issues),
            "info_count": sum(issue.severity is IssueSeverity.INFO for issue in issues),
            "extracted_chars": sum(page.extracted_chars for page in page_reports),
            "document_kind": inspection.kind.value,
            "mean_page_score": round(mean_score, 2),
            "low_decile_score": round(low_score, 2),
        }
    )
    return QualityReport(
        score=round(overall, 2),
        passed=overall >= minimum_score
        and not any(issue.severity is IssueSeverity.ERROR for issue in issues),
        primary_engine=primary_engine,
        fallback_pages=fallback_pages,
        issues=issues,
        pages=page_reports,
        metrics=metrics,
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _quality_text(block) -> str:
    if block.type is BlockType.TABLE and block.table_html:
        visible = re.sub(r"<[^>]+>", " ", block.table_html)
        return html.unescape(re.sub(r"\s+", " ", visible)).strip()
    return block.text


def _has_trailing_line_numbers(text: str) -> bool:
    match = _TRAILING_NUMBER_RUN.search(text)
    if not match:
        return False
    numbers = [int(value) for value in match.group(1).split()]
    return all(right == left + 1 for left, right in zip(numbers, numbers[1:], strict=False))


def _is_collapsed_code(block: Any) -> bool:
    compact = block.text.strip()
    if "\n" in compact or len(compact) < 60:
        return False
    metadata = block.metadata or {}
    # A single numbered logical line can wrap across several visual PDF lines.
    # Layout recovery intentionally rejoins those continuations; treating the
    # resulting long repr/output as collapsed code is a false positive.
    if (
        metadata.get("layout_recovered")
        and _safe_int(metadata.get("start_line")) is not None
        and _safe_int(metadata.get("start_line")) == _safe_int(metadata.get("end_line"))
    ):
        return False
    if len(_CODE_STATEMENT_SIGNAL.findall(compact)) < 3 or infer_code_kind(compact):
        return False
    language = normalize_code_language(str(metadata.get("language", "") or ""))
    language = language or infer_code_language(compact)
    if language != "python":
        return False
    try:
        ast.parse(compact)
    except (SyntaxError, ValueError, TypeError):
        return True
    return False


def _is_orphan_code_fragment(text: str) -> bool:
    compact = text.strip()
    return 0 < len(compact) <= 4 and _ORPHAN_CODE_FRAGMENT.fullmatch(compact) is not None


def _analyze_code_quality(
    document: ParsedDocument,
) -> tuple[dict[int, list[_CodeFinding]], dict[str, int | float]]:
    groups = _logical_code_groups(document)
    findings: dict[int, list[_CodeFinding]] = {}
    checked = 0
    invalid = 0
    untyped = 0
    inferred_language = 0
    reclassified_language = 0
    source_lines = 0
    unclosed_fences = 0

    for group in groups:
        text = group.text
        declared_language = normalize_code_language(group.language)
        language = resolve_code_language(
            text,
            [
                (group.language, "raw_markdown" if group.raw_markdown else None),
                *[
                    (
                        str(metadata.get("language", "") or ""),
                        str(metadata.get("language_source", "") or ""),
                    )
                    for metadata in group.metadata
                ],
            ],
        )
        if not declared_language:
            untyped += 1
            if not language and _looks_like_python_source(text, group):
                language = "python"
            if language:
                inferred_language += 1
        elif language and language != declared_language:
            reclassified_language += 1
        if any(metadata.get("unclosed_fence") for metadata in group.metadata):
            unclosed_fences += 1
            checked += 1
            invalid += 1
            issue = QualityIssue(
                code="unclosed_code_fence",
                severity=IssueSeverity.ERROR,
                page=group.first_page,
                block_id=group.first_block_id,
                message="原始 Markdown 中的代码围栏没有闭合",
            )
            findings.setdefault(group.first_page, []).append(_CodeFinding(issue, 25, True))
            continue

        if language in {"python", "py", "python3"}:
            if not _looks_like_python_source(text, group):
                continue
            checked += 1
            source_lines += max(1, text.count("\n") + 1)
            try:
                ast.parse(text)
            except (SyntaxError, ValueError, TypeError) as exc:
                invalid += 1
                line_number = getattr(exc, "lineno", None)
                page = group.page_for_line(line_number)
                line_count = max(1, text.count("\n") + 1)
                is_error = line_count >= 8 or len(set(group.pages)) > 1 or group.raw_markdown
                severity = IssueSeverity.ERROR if is_error else IssueSeverity.WARNING
                reason = getattr(exc, "msg", exc.__class__.__name__)
                code = (
                    "image_ocr_syntax_warning"
                    if any(metadata.get("image_layout_recovered") for metadata in group.metadata)
                    else "python_syntax_error"
                )
                issue = QualityIssue(
                    code=code,
                    severity=severity,
                    page=page,
                    block_id=group.first_block_id,
                    message=(f"Python 代码未通过语法检查（逻辑行 {line_number or '?'}：{reason}）"),
                )
                findings.setdefault(page, []).append(
                    _CodeFinding(issue, 25 if is_error else 8, is_error)
                )
        elif language == "json":
            checked += 1
            source_lines += max(1, text.count("\n") + 1)
            try:
                json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                invalid += 1
                line_number = getattr(exc, "lineno", None)
                page = group.page_for_line(line_number)
                line_count = max(1, text.count("\n") + 1)
                is_error = line_count >= 8 or group.raw_markdown
                issue = QualityIssue(
                    code="json_syntax_error",
                    severity=IssueSeverity.ERROR if is_error else IssueSeverity.WARNING,
                    page=page,
                    block_id=group.first_block_id,
                    message=f"JSON 代码未通过结构检查（逻辑行 {line_number or '?'}）",
                )
                findings.setdefault(page, []).append(
                    _CodeFinding(issue, 20 if is_error else 6, is_error)
                )

    code_quality_score = round((checked - invalid) / checked * 100, 2) if checked else 100.0
    return findings, {
        "logical_code_block_count": len(groups),
        "checked_code_block_count": checked,
        "invalid_code_block_count": invalid,
        "untyped_code_block_count": untyped,
        "inferred_language_code_block_count": inferred_language,
        "reclassified_language_code_block_count": reclassified_language,
        "source_code_line_count": source_lines,
        "unclosed_code_fence_count": unclosed_fences,
        "code_quality_score": code_quality_score,
    }


def _logical_code_groups(document: ParsedDocument) -> list[_LogicalCode]:
    groups: list[_LogicalCode] = []
    for page in document.pages:
        for block in page.blocks:
            if block.type is BlockType.CODE:
                language = str((block.metadata or {}).get("language", "") or "")
                if (
                    block.metadata.get("continues_previous")
                    and groups
                    and not groups[-1].raw_markdown
                ):
                    group = groups[-1]
                    group.pages.append(page.number)
                    group.parts.append(block.text)
                    group.block_ids.append(block.id)
                    group.metadata.append(dict(block.metadata or {}))
                    if not group.language and language:
                        group.language = language
                else:
                    groups.append(
                        _LogicalCode(
                            language=language,
                            pages=[page.number],
                            parts=[block.text],
                            block_ids=[block.id],
                            metadata=[dict(block.metadata or {})],
                        )
                    )
            elif block.type is BlockType.RAW_MARKDOWN:
                groups.extend(_raw_markdown_code_groups(block))
    return groups


def _raw_markdown_code_groups(block: Any) -> list[_LogicalCode]:
    groups: list[_LogicalCode] = []
    fence_character = ""
    fence_length = 0
    language = ""
    body: list[str] = []
    for line in block.text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if not fence_character:
            match = _FENCE_START.match(line)
            if match is None:
                continue
            fence = match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            language = match.group("info").strip().split(maxsplit=1)[0].casefold()
            body = []
            continue
        if re.fullmatch(
            rf"\s*{re.escape(fence_character)}{{{fence_length},}}\s*",
            line,
        ):
            groups.append(
                _LogicalCode(
                    language=language,
                    pages=[block.page],
                    parts=["\n".join(body)],
                    block_ids=[block.id],
                    metadata=[dict(block.metadata or {})],
                    raw_markdown=True,
                )
            )
            fence_character = ""
            fence_length = 0
            language = ""
            body = []
        else:
            body.append(line)
    if fence_character:
        metadata = dict(block.metadata or {})
        metadata["unclosed_fence"] = True
        groups.append(
            _LogicalCode(
                language=language,
                pages=[block.page],
                parts=["\n".join(body)],
                block_ids=[block.id],
                metadata=[metadata],
                raw_markdown=True,
            )
        )
    return groups


def _docling_confidence_metrics(
    document: ParsedDocument,
) -> dict[str, int | float | str | bool]:
    """Expose compact Docling confidence diagnostics without trusting them alone."""

    confidence = document.metadata.get("docling_confidence")
    if not isinstance(confidence, Mapping):
        return {}

    metrics: dict[str, int | float | str | bool] = {}
    for key, value in confidence.items():
        if isinstance(value, (str, bool, int)) or (
            isinstance(value, float) and math.isfinite(value)
        ):
            metrics[f"docling_{key}"] = value

    pages = confidence.get("pages")
    page_values: list[Mapping[Any, Any]] = []
    if isinstance(pages, Mapping):
        page_values = [value for value in pages.values() if isinstance(value, Mapping)]
    elif isinstance(pages, list):
        page_values = [value for value in pages if isinstance(value, Mapping)]

    numeric_by_name: dict[str, list[float]] = {}
    for page in page_values:
        for key, value in page.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            number = float(value)
            if math.isfinite(number):
                numeric_by_name.setdefault(str(key), []).append(number)
    for key, values in numeric_by_name.items():
        metrics[f"docling_mean_{key}"] = round(sum(values) / len(values), 4)
        metrics[f"docling_low_{key}"] = round(min(values), 4)
    return metrics


def _looks_like_python_source(text: str, group: _LogicalCode) -> bool:
    if infer_code_kind(text) == "output" or any(
        metadata.get("code_kind") == "output" for metadata in group.metadata
    ):
        return False
    inline_repr = any(
        len(re.findall(r"\b[A-Za-z_]\w*\s*=", line)) >= 2 and ";" not in line
        for line in text.splitlines()
    )
    if (_CONSOLE_OUTPUT_SIGNAL.search(text) or inline_repr) and not _PYTHON_SOURCE_START.search(
        text
    ):
        return False
    if _PYTHON_SOURCE_START.search(text):
        return True
    return len(_PYTHON_SOURCE_SIGNAL.findall(text)) >= 2


def _is_aligned_image_code_recovery(block: Any) -> bool:
    metadata = block.metadata or {}
    return (
        bool(metadata.get("image_layout_recovered"))
        and _safe_float(metadata.get("image_layout_confidence")) >= 0.9
        and _safe_float(metadata.get("image_layout_similarity")) >= 0.9
    )


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _document_metrics(document: ParsedDocument) -> dict[str, int]:
    blocks = [block for page in document.pages for block in page.blocks]
    code_blocks = [block for block in blocks if block.type is BlockType.CODE]
    table_blocks = [block for block in blocks if block.type is BlockType.TABLE]
    image_blocks = [block for block in blocks if block.type is BlockType.IMAGE and block.asset_path]
    return {
        "block_count": len(blocks),
        "heading_count": sum(
            block.type in {BlockType.TITLE, BlockType.HEADING} for block in blocks
        ),
        "list_item_count": sum(block.type is BlockType.LIST_ITEM for block in blocks),
        "table_count": len(table_blocks),
        "html_table_count": sum(bool(block.table_html) for block in table_blocks),
        "repaired_table_count": sum(
            bool(block.metadata.get("table_alignment_repaired")) for block in table_blocks
        ),
        "code_block_count": len(code_blocks),
        "layout_recovered_code_blocks": sum(
            bool(block.metadata.get("layout_recovered")) for block in code_blocks
        ),
        "image_ocr_code_blocks": sum(
            bool(block.metadata.get("image_layout_recovered")) for block in code_blocks
        ),
        "image_count": len(image_blocks),
        "formula_count": sum(block.type is BlockType.FORMULA for block in blocks),
    }


def _flat_heading_findings(document: ParsedDocument) -> tuple[set[int], int]:
    headings = [
        block
        for page in document.pages
        for block in page.blocks
        if block.type in {BlockType.TITLE, BlockType.HEADING}
    ]
    if len(headings) < 8:
        return set(), 0

    levels = Counter(
        1 if block.type is BlockType.TITLE else (block.level or 2) for block in headings
    )
    dominant_ratio = max(levels.values()) / len(headings)
    if dominant_ratio < 0.9 or len(levels) > 2:
        return set(), 0

    mismatches = []
    for block in headings:
        match = _NUMBERED_HEADING.match(block.text)
        if not match or "." not in match.group(0):
            continue
        number_depth = match.group(0).count(".") + 1
        expected_level = min(6, number_depth + 1)
        actual_level = 1 if block.type is BlockType.TITLE else (block.level or 2)
        if actual_level != expected_level:
            mismatches.append(block)

    if len(mismatches) < 4:
        return set(), 0
    return {block.page for block in mismatches}, len(mismatches)


def _is_tiny_asset(block: Any, asset: Path) -> bool:
    if block.bbox is not None:
        width = abs(block.bbox.right - block.bbox.left)
        height = abs(block.bbox.top - block.bbox.bottom)
        if width <= 24 and height <= 24:
            return True
    try:
        from PIL import Image

        with Image.open(asset) as image:
            width, height = image.size
        return width <= 24 and height <= 24
    except (ImportError, OSError, ValueError):
        return False


def _table_structure_mismatch(block: Any) -> bool:
    metadata = block.metadata or {}
    structure = metadata.get("table_structure")
    if isinstance(structure, dict):
        metadata = {**metadata, **structure}
    expected_rows = _metadata_integer(
        metadata, ("table_rows", "num_rows", "row_count", "n_rows", "rows")
    )
    expected_columns = _metadata_integer(
        metadata,
        ("table_columns", "num_cols", "column_count", "n_cols", "columns", "cols"),
    )

    if not block.table_html:
        return bool(block.text) and not is_valid_gfm_table(
            block.text,
            expected_rows=expected_rows,
            expected_columns=expected_columns,
        )
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", block.table_html, flags=re.IGNORECASE | re.DOTALL)
    column_counts: list[int] = []
    for row in rows:
        count = 0
        for tag in re.findall(r"<t[dh]\b[^>]*>", row, flags=re.IGNORECASE):
            colspan = re.search(r"\bcolspan=[\"']?(\d+)", tag, flags=re.IGNORECASE)
            count += int(colspan.group(1)) if colspan else 1
        column_counts.append(count)

    if expected_rows is not None and expected_rows != len(rows):
        return True
    if expected_columns is not None and any(count != expected_columns for count in column_counts):
        return True
    if "rowspan=" not in block.table_html.lower() and len(set(column_counts)) > 1:
        return True
    return False


def _has_structured_table_metadata(block: Any) -> bool:
    metadata = block.metadata or {}
    structure = metadata.get("table_structure")
    if isinstance(structure, dict):
        metadata = {**metadata, **structure}
    rows = _metadata_integer(metadata, ("table_rows", "num_rows", "row_count", "n_rows"))
    columns = _metadata_integer(
        metadata, ("table_columns", "num_cols", "column_count", "n_cols", "cols")
    )
    return bool(rows and columns and (block.text or block.table_html))


def _metadata_integer(metadata: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None
