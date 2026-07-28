from __future__ import annotations

import re
from pathlib import Path

from pdfmd.code_analysis import resolve_code_language
from pdfmd.models import BlockType, ConversionOptions, DocumentBlock, ParsedDocument


class MarkdownRenderer:
    def render(self, document: ParsedDocument, options: ConversionOptions) -> str:
        parts: list[str] = []
        if options.include_front_matter:
            parts.append(self._front_matter(document))

        # Continuations are a document-level relationship: a page marker must
        # never split a logical code block into multiple Markdown fences.
        blocks = _merge_code_continuations(
            [block for page in document.pages for block in page.blocks]
        )
        if options.preserve_page_markers:
            blocks_by_page: dict[int, list[DocumentBlock]] = {}
            for block in blocks:
                blocks_by_page.setdefault(block.page, []).append(block)
            for page in document.pages:
                parts.append(f"<!-- page: {page.number} -->")
                self._append_blocks(
                    parts,
                    blocks_by_page.pop(page.number, []),
                    annotate_code_pages=True,
                )
            # Be defensive about parser output whose block page is missing from
            # document.pages; retain both the block and its provenance.
            for page_number, page_blocks in blocks_by_page.items():
                parts.append(f"<!-- page: {page_number} -->")
                self._append_blocks(parts, page_blocks, annotate_code_pages=True)
        else:
            self._append_blocks(parts, blocks)

        return _normalize_spacing("\n".join(parts))

    def _append_blocks(
        self,
        parts: list[str],
        blocks: list[DocumentBlock],
        *,
        annotate_code_pages: bool = False,
    ) -> None:
        list_open = False
        for block in blocks:
            rendered = self._render_block(block)
            if not rendered:
                continue
            if annotate_code_pages:
                page_comment = _code_page_comment(block)
                if page_comment:
                    parts.extend([page_comment, ""])
            if block.type is BlockType.LIST_ITEM:
                if not list_open and parts and parts[-1] != "":
                    parts.append("")
                parts.append(rendered)
                list_open = True
            else:
                list_open = False
                parts.extend([rendered, ""])
        if parts and parts[-1] != "":
            parts.append("")

    def _front_matter(self, document: ParsedDocument) -> str:
        title = _yaml_string(document.title or Path(document.source_filename).stem)
        filename = _yaml_string(document.source_filename)
        return "\n".join(
            [
                "---",
                f"title: {title}",
                f"source: {filename}",
                f"pages: {document.page_count}",
                f"document_kind: {document.kind.value}",
                f"source_sha256: {document.source_sha256}",
                "---",
            ]
        )

    def _render_block(self, block: DocumentBlock) -> str:
        if block.type is BlockType.RAW_MARKDOWN:
            return block.text.strip()
        text = _clean_code(block.text) if block.type is BlockType.CODE else _clean_text(block.text)
        if block.type is BlockType.TITLE:
            return f"# {_single_line(text)}"
        if block.type is BlockType.HEADING:
            return f"{'#' * (block.level or 2)} {_single_line(text)}"
        if block.type is BlockType.PARAGRAPH:
            return text
        if block.type is BlockType.LIST_ITEM:
            marker = _list_marker(block)
            indent = "  " * max(0, min(6, int(block.metadata.get("indent_level", 0) or 0)))
            return f"{indent}{marker} {text}"
        if block.type is BlockType.TABLE:
            return block.table_html.strip() if block.table_html else text
        if block.type is BlockType.FORMULA:
            formula = text.removeprefix("$$").removesuffix("$$").strip()
            return f"$$\n{formula}\n$$"
        if block.type is BlockType.CODE:
            language = re.sub(r"[^a-zA-Z0-9_+-]", "", str(block.metadata.get("language", "")))
            fence = _safe_code_fence(text)
            return f"{fence}{language}\n{text}\n{fence}"
        if block.type is BlockType.IMAGE:
            if not block.asset_path:
                return ""
            alt = _single_line(text or "图片").replace("]", "\\]")
            path = block.asset_path.replace(" ", "%20")
            return f"![{alt}]({path})"
        if block.type is BlockType.CAPTION:
            return f"*{text}*"
        if block.type is BlockType.FOOTNOTE:
            return f"> {text}"
        if block.type in {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}:
            return ""
        return text


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line).strip()
    # Layout engines often insert a space where a Chinese word wrapped in the
    # source PDF (e.g. "规 则").  Removing only CJK-to-CJK spaces avoids
    # disturbing intentional spaces around English identifiers.
    return re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", cleaned)


def _clean_code(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _list_marker(block: DocumentBlock) -> str:
    marker = str(block.metadata.get("marker", "") or "").strip()
    if block.metadata.get("ordered"):
        return marker if re.fullmatch(r"\d+[.)]", marker) else "1."
    return marker if marker in {"-", "*", "+"} else "-"


def _merge_code_continuations(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    merged: list[DocumentBlock] = []
    last_code_index: int | None = None
    for block in blocks:
        if (
            block.type is BlockType.CODE
            and block.metadata.get("continues_previous")
            and last_code_index is not None
        ):
            previous = merged[last_code_index]
            intervening = merged[last_code_index + 1 :]
            noise_removed = sum(_is_continuation_noise(item) for item in intervening)
            if noise_removed:
                merged = merged[: last_code_index + 1] + [
                    item for item in intervening if not _is_continuation_noise(item)
                ]
            metadata = dict(previous.metadata)
            pages = list(metadata.get("continued_pages", [previous.page]))
            if block.page not in pages:
                pages.append(block.page)
            metadata["continued_pages"] = pages
            if noise_removed:
                metadata["continuation_noise_removed"] = (
                    int(metadata.get("continuation_noise_removed", 0) or 0) + noise_removed
                )
            continuation = block.text.lstrip("\n\r")
            combined_text = f"{previous.text.rstrip()}\n{continuation}"
            language = resolve_code_language(
                combined_text,
                [
                    (
                        str(previous.metadata.get("language", "") or ""),
                        str(previous.metadata.get("language_source", "") or ""),
                    ),
                    (
                        str(block.metadata.get("language", "") or ""),
                        str(block.metadata.get("language_source", "") or ""),
                    ),
                ],
            )
            if language:
                previous_language = str(previous.metadata.get("language", "") or "")
                continuation_language = str(block.metadata.get("language", "") or "")
                metadata["language"] = language
                if language == previous_language and previous.metadata.get("language_source"):
                    metadata["language_source"] = previous.metadata["language_source"]
                elif language == continuation_language and block.metadata.get("language_source"):
                    metadata["language_source"] = block.metadata["language_source"]
                else:
                    metadata["language_source"] = "merged_inference"
            merged[last_code_index] = previous.model_copy(
                update={
                    "text": combined_text,
                    "metadata": metadata,
                }
            )
        else:
            merged.append(block)
            if block.type is BlockType.CODE:
                last_code_index = len(merged) - 1
            elif block.type is BlockType.RAW_MARKDOWN:
                last_code_index = None
    return merged


def _is_continuation_noise(block: DocumentBlock) -> bool:
    return block.type in {BlockType.LIST_ITEM, BlockType.PARAGRAPH} and bool(
        re.fullmatch(r"\d{1,4}", block.text.strip())
    )


def _code_page_comment(block: DocumentBlock) -> str:
    if block.type is not BlockType.CODE:
        return ""
    pages = block.metadata.get("continued_pages")
    if not isinstance(pages, (list, tuple)) or len(pages) < 2:
        return ""
    page_labels = ", ".join(str(page) for page in pages)
    return f"<!-- code spans pages: {page_labels} -->"


def _single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lstrip("#").strip()


def _safe_code_fence(text: str) -> str:
    longest_run = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest_run + 1)


def _yaml_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def _normalize_spacing(markdown: str) -> str:
    markdown = re.sub(r"\n{4,}", "\n\n\n", markdown).strip()
    return f"{markdown}\n"
