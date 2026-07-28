from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pdfmd.config import Settings
from pdfmd.engines import get_parser
from pdfmd.models import (
    ConversionOptions,
    ConversionResult,
    IssueSeverity,
    ParsedDocument,
    QualityIssue,
    QualityReport,
)
from pdfmd.parsers.base import ParserError
from pdfmd.postprocess import normalize_document
from pdfmd.quality import evaluate_quality
from pdfmd.renderer import MarkdownRenderer
from pdfmd.security import inspect_pdf

ProgressCallback = Callable[[int, str], None]


class ConversionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.renderer = MarkdownRenderer()

    def convert(
        self,
        pdf_path: Path,
        output_dir: Path,
        options: ConversionOptions,
        *,
        job_id: str | None = None,
        source_filename: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> ConversionResult:
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_previous_artifacts(output_dir)
        self._progress(progress, 3, "检查 PDF 文件")
        inspection = inspect_pdf(
            pdf_path,
            max_file_size=self.settings.max_file_size,
            max_pages=self.settings.max_pages,
            password=options.password,
        )
        if source_filename is not None:
            inspection.filename = source_filename
        self._progress(progress, 10, f"识别为 {inspection.kind.value}")

        requested_primary_name = options.primary_engine
        primary_name = requested_primary_name
        primary = get_parser(primary_name)
        availability_issue: QualityIssue | None = None
        primary_retry_count = 0
        if not primary.available():
            availability_issue = QualityIssue(
                code="primary_engine_unavailable",
                severity=IssueSeverity.WARNING,
                message=f"{primary_name} 不可用，已使用 native 引擎",
            )
            primary_name = "native"
            primary = get_parser(primary_name)

        def parser_progress(value: int, stage: str) -> None:
            self._progress(progress, 12 + int(value * 0.5), stage)

        try:
            document = primary.parse(
                pdf_path,
                output_dir,
                inspection,
                options,
                progress=parser_progress,
            )
        except ParserError as first_exc:
            if primary.name == "native":
                raise
            exc: ParserError | None = first_exc
            if _is_transient_parser_error(first_exc):
                primary_retry_count = 1
                self._progress(progress, 16, "主引擎临时中断，正在重试")
                try:
                    document = primary.parse(
                        pdf_path,
                        output_dir,
                        inspection,
                        options,
                        progress=parser_progress,
                    )
                except ParserError as retry_exc:
                    exc = retry_exc
                else:
                    exc = None
            if exc is not None:
                availability_issue = QualityIssue(
                    code="primary_engine_failed",
                    severity=IssueSeverity.WARNING,
                    message=f"{primary.name} 解析失败，已使用 native 引擎：{exc}",
                )
                primary_name = "native"
                primary = get_parser(primary_name)
                self._progress(progress, 18, "主引擎失败，切换 Native")
                document = primary.parse(
                    pdf_path,
                    output_dir,
                    inspection,
                    options,
                    progress=parser_progress,
                )
        document = normalize_document(document)
        self._progress(progress, 64, "检查解析质量")
        quality = evaluate_quality(
            document,
            inspection,
            output_dir,
            minimum_score=options.minimum_quality_score,
            primary_engine=primary_name,
        )
        if availability_issue:
            quality.issues.insert(0, availability_issue)

        attempted_fallback_pages: list[int] = []
        fallback_name: str | None = None
        applied_fallback_pages: list[int] = []
        if options.enable_quality_fallback and quality.fallback_pages:
            fallback = get_parser(options.fallback_engine)
            if fallback.available() and fallback.name != primary.name:
                attempted_fallback_pages = list(quality.fallback_pages)
                fallback_name = fallback.name
                self._progress(progress, 68, "低质量页面进入 OCR 兜底")

                def fallback_progress(value: int, stage: str) -> None:
                    self._progress(progress, 68 + int(value * 0.17), stage)

                try:
                    fallback_document = fallback.parse(
                        pdf_path,
                        output_dir,
                        inspection,
                        options,
                        pages=attempted_fallback_pages,
                        progress=fallback_progress,
                    )
                    fallback_document = normalize_document(fallback_document)
                    document, applied_fallback_pages = self._select_better_pages(
                        document,
                        fallback_document,
                        inspection,
                        output_dir,
                        options.minimum_quality_score,
                    )
                    quality = evaluate_quality(
                        document,
                        inspection,
                        output_dir,
                        minimum_score=options.minimum_quality_score,
                        primary_engine=primary_name,
                    )
                    quality.fallback_engine = fallback_name
                    quality.fallback_pages = applied_fallback_pages
                    quality.metrics["fallback_attempted_pages"] = ",".join(
                        map(str, attempted_fallback_pages)
                    )
                    quality.issues.insert(
                        0,
                        QualityIssue(
                            code="fallback_applied",
                            severity=IssueSeverity.INFO,
                            message=(
                                f"{fallback_name} 已重试 {len(attempted_fallback_pages)} 页，"
                                f"采用其中 {len(applied_fallback_pages)} 页"
                            ),
                        ),
                    )
                except ParserError as exc:
                    quality.issues.append(
                        QualityIssue(
                            code="fallback_failed",
                            severity=IssueSeverity.WARNING,
                            message=str(exc),
                        )
                    )
            elif options.fallback_engine != "native":
                quality.issues.append(
                    QualityIssue(
                        code="fallback_engine_unavailable",
                        severity=IssueSeverity.WARNING,
                        message=f"{options.fallback_engine} 未安装，低质量页面未重新解析",
                    )
                )

        if availability_issue and not any(
            issue.code == availability_issue.code for issue in quality.issues
        ):
            quality.issues.insert(0, availability_issue)

        quality.metrics["requested_primary_engine"] = requested_primary_name
        quality.metrics["primary_retry_count"] = primary_retry_count
        if availability_issue:
            # A readable Native result is useful, but it has not satisfied the
            # structured-parser contract the user requested. Do not let a
            # numeric score turn that degraded conversion into a false pass.
            quality.passed = False
            quality.metrics["degraded_conversion"] = True
        else:
            quality.metrics["degraded_conversion"] = False
        _refresh_issue_metrics(quality)

        self._progress(progress, 88, "生成 Markdown 与质量报告")
        markdown = self.renderer.render(document, options)
        markdown_path = output_dir / "document.md"
        document_path = output_dir / "document.json"
        quality_path = output_dir / "quality-report.json"
        manifest_path = output_dir / "manifest.json"

        _atomic_write(markdown_path, markdown)
        _atomic_write(document_path, document.model_dump_json(indent=2))
        _atomic_write(quality_path, quality.model_dump_json(indent=2))
        artifact_files = [
            "document.md",
            "document.json",
            "quality-report.json",
            "manifest.json",
        ]
        assets_dir = output_dir / "assets"
        if assets_dir.is_dir():
            artifact_files.extend(
                str(path.relative_to(output_dir))
                for path in sorted(assets_dir.rglob("*"))
                if path.is_file()
            )
        manifest = {
            "schema_version": "1.0",
            "source_filename": inspection.filename,
            "source_sha256": inspection.sha256,
            "page_count": inspection.page_count,
            "document_kind": inspection.kind.value,
            "primary_engine": primary_name,
            "fallback_engine": fallback_name,
            "fallback_attempted_pages": attempted_fallback_pages,
            "fallback_pages": applied_fallback_pages,
            "quality_score": quality.score,
            "quality_passed": quality.passed,
            "degraded_conversion": bool(availability_issue),
            "primary_retry_count": primary_retry_count,
            "files": artifact_files,
        }
        _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
        self._progress(progress, 100, "转换完成")

        return ConversionResult(
            job_id=job_id,
            output_dir=str(output_dir),
            markdown_path=str(markdown_path),
            document_path=str(document_path),
            quality_path=str(quality_path),
            markdown=markdown,
            document=document,
            quality=quality,
        )

    @staticmethod
    def _select_better_pages(
        primary: ParsedDocument,
        fallback: ParsedDocument,
        inspection,
        output_dir: Path,
        minimum_score: float,
    ) -> tuple[ParsedDocument, list[int]]:
        primary_pages = {page.number: page for page in primary.pages}
        fallback_pages = {page.number: page for page in fallback.pages}
        primary_report = evaluate_quality(
            primary,
            inspection,
            output_dir,
            minimum_score=minimum_score,
            primary_engine=str(primary.metadata.get("engine", "primary")),
        )
        primary_scores = {page.page: page.score for page in primary_report.pages}

        applied_pages: list[int] = []
        for page_number, fallback_page in fallback_pages.items():
            candidate_doc = primary.model_copy(deep=True)
            candidate_pages = {page.number: page for page in candidate_doc.pages}
            candidate_pages[page_number] = fallback_page
            candidate_doc.pages = sorted(candidate_pages.values(), key=lambda page: page.number)
            candidate_report = evaluate_quality(
                candidate_doc,
                inspection,
                output_dir,
                minimum_score=minimum_score,
                primary_engine=fallback_page.engine,
            )
            candidate_score = next(
                page.score for page in candidate_report.pages if page.page == page_number
            )
            if candidate_score > primary_scores.get(page_number, 0):
                primary_pages[page_number] = fallback_page
                applied_pages.append(page_number)

        merged = primary.model_copy(deep=True)
        merged.pages = sorted(primary_pages.values(), key=lambda page: page.number)
        merged.metadata["page_engines"] = {str(page.number): page.engine for page in merged.pages}
        return merged, applied_pages

    @staticmethod
    def _progress(callback: ProgressCallback | None, value: int, stage: str) -> None:
        if callback:
            callback(max(0, min(100, value)), stage)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _cleanup_previous_artifacts(output_dir: Path) -> None:
    """Remove only files recorded by a previous PDF Markdown Studio run."""

    known_root_files = {
        "document.md",
        "document.json",
        "quality-report.json",
        "manifest.json",
    }
    recorded: list[str] = []
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files", [])
            if isinstance(files, list):
                recorded = [item for item in files if isinstance(item, str)]
        except (OSError, ValueError):
            recorded = []

    candidates = set(known_root_files)
    candidates.update(name for name in recorded if Path(name).parts[:1] == ("assets",))
    root = output_dir.resolve()
    for name in candidates:
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file():
            target.unlink(missing_ok=True)

    assets_dir = output_dir / "assets"
    if assets_dir.is_dir():
        for directory in sorted(
            (path for path in assets_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            assets_dir.rmdir()
        except OSError:
            pass


def _refresh_issue_metrics(quality: QualityReport) -> None:
    """Keep summary counters consistent with issues added by conversion orchestration."""

    quality.metrics["issue_count"] = len(quality.issues)
    quality.metrics["error_count"] = sum(
        issue.severity is IssueSeverity.ERROR for issue in quality.issues
    )
    quality.metrics["warning_count"] = sum(
        issue.severity is IssueSeverity.WARNING for issue in quality.issues
    )
    quality.metrics["info_count"] = sum(
        issue.severity is IssueSeverity.INFO for issue in quality.issues
    )


def _is_transient_parser_error(exc: ParserError) -> bool:
    """Return whether retrying the same parser once is likely to help."""

    message = str(exc).casefold()
    return any(
        signal in message
        for signal in (
            "broken pipe",
            "connection reset",
            "resource temporarily unavailable",
            "temporarily unavailable",
            "unexpected eof",
        )
    )
