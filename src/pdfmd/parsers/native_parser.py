from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from pypdf import PdfReader

from pdfmd.layout_recovery import recover_code_layout
from pdfmd.models import (
    BlockType,
    ConversionOptions,
    DocumentBlock,
    DocumentPage,
    ParsedDocument,
    PdfInspection,
)
from pdfmd.parsers.base import BaseParser, ProgressCallback


class NativePdfParser(BaseParser):
    """Dependency-light parser for born-digital PDFs and emergency fallback."""

    name = "native"

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("pypdf") is not None

    def parse(
        self,
        pdf_path: Path,
        output_dir: Path,
        inspection: PdfInspection,
        options: ConversionOptions,
        *,
        pages: list[int] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParsedDocument:
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        reader = PdfReader(str(pdf_path), strict=False)
        if reader.is_encrypted and options.password:
            reader.decrypt(options.password)

        selected = set(pages or range(1, len(reader.pages) + 1))
        blocks_by_page: dict[int, list[DocumentBlock]] = {}
        page_inspections = {page.number: page for page in inspection.pages}
        for page_number, page in enumerate(reader.pages, start=1):
            if page_number not in selected:
                continue
            blocks: list[DocumentBlock] = []
            try:
                raw_text = page.extract_text(extraction_mode="layout") or ""
            except Exception:
                raw_text = page.extract_text() or ""

            for block_index, paragraph in enumerate(_paragraphs(raw_text), start=1):
                block_type, level, text = _classify_paragraph(
                    paragraph,
                    page_number=page_number,
                    block_index=block_index,
                )
                blocks.append(
                    DocumentBlock(
                        id=f"p{page_number}-text-{block_index}",
                        type=block_type,
                        page=page_number,
                        text=text,
                        level=level,
                        engine=self.name,
                    )
                )

            if options.extract_images:
                try:
                    images = page.images
                except Exception:
                    images = []
                for image_index, image in enumerate(images, start=1):
                    suffix = Path(image.name).suffix.lower() or ".bin"
                    safe_name = f"page-{page_number:04d}-image-{image_index:03d}{suffix}"
                    image_path = assets_dir / safe_name
                    try:
                        image_path.write_bytes(image.data)
                    except Exception:
                        continue
                    blocks.append(
                        DocumentBlock(
                            id=f"p{page_number}-image-{image_index}",
                            type=BlockType.IMAGE,
                            page=page_number,
                            text=f"第 {page_number} 页图片 {image_index}",
                            asset_path=f"assets/{safe_name}",
                            engine=self.name,
                        )
                    )

            blocks_by_page[page_number] = blocks
            if progress:
                progress(int(page_number / inspection.page_count * 90), f"解析第 {page_number} 页")

        layout_recovery: dict[str, int | str] = {}
        if inspection.kind.value in {"born_digital", "mixed"}:
            try:
                stats = recover_code_layout(pdf_path, blocks_by_page)
                layout_recovery = {
                    "pages_processed": stats.pages_processed,
                    "candidates_found": stats.candidates_found,
                    "unnumbered_candidates_found": stats.unnumbered_candidates_found,
                    "blocks_recovered": stats.blocks_recovered,
                    "fragments_merged": stats.fragments_merged,
                    "duplicate_fragments_removed": stats.duplicate_fragments_removed,
                    "paragraphs_promoted": stats.paragraphs_promoted,
                    "cross_page_continuations": stats.cross_page_continuations,
                    "line_number_tails_removed": stats.line_number_tails_removed,
                    "layout_tables_recovered": stats.layout_tables_recovered,
                    "image_candidates_found": stats.image_candidates_found,
                    "embedded_images_found": stats.embedded_images_found,
                    "image_ocr_pages": stats.image_ocr_pages,
                    "image_blocks_recovered": stats.image_blocks_recovered,
                }
            except Exception as exc:
                layout_recovery = {"error": exc.__class__.__name__}

        document_pages = [
            DocumentPage(
                number=page_number,
                width=page_inspections[page_number].width,
                height=page_inspections[page_number].height,
                blocks=blocks,
                engine=self.name,
                source_text_chars=page_inspections[page_number].native_text_chars,
                source_image_count=page_inspections[page_number].image_count,
            )
            for page_number, blocks in sorted(blocks_by_page.items())
        ]
        if progress:
            progress(100, "Native 结构恢复完成")

        return ParsedDocument(
            source_filename=inspection.filename,
            source_sha256=inspection.sha256,
            title=inspection.title,
            page_count=inspection.page_count,
            kind=inspection.kind,
            pages=document_pages,
            metadata={
                "engine": self.name,
                "author": inspection.author,
                "layout_recovery": layout_recovery,
            },
        )


def _paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"\n\s*\n", text)
    result: list[str] = []
    for chunk in chunks:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        # Preserve list-like lines separately; join ordinary wrapped lines.
        buffer: list[str] = []
        for line in lines:
            if re.match(r"^(?:[-*•]|\d+[.)])\s+", line):
                if buffer:
                    result.append(_join_wrapped_lines(buffer))
                    buffer = []
                result.append(line)
            else:
                buffer.append(line)
        if buffer:
            result.append(_join_wrapped_lines(buffer))
    return result


def _classify_paragraph(
    text: str,
    *,
    page_number: int = 1,
    block_index: int = 1,
) -> tuple[BlockType, int | None, str]:
    list_match = re.match(r"^(?:[-*•]|(\d+)[.)])\s+(.*)$", text, flags=re.S)
    if list_match:
        return BlockType.LIST_ITEM, None, list_match.group(2).strip()

    compact = re.sub(r"\s+", " ", text).strip()
    if page_number == 1 and block_index == 1 and len(compact) <= 120:
        return BlockType.TITLE, 1, compact
    if re.match(r"^第\s*\d+\s*章", compact):
        return BlockType.HEADING, 1, compact
    decimal = re.match(r"^(\d+(?:\.\d+)+)(?:\s|$)", compact)
    if decimal:
        return BlockType.HEADING, min(6, decimal.group(1).count(".") + 2), compact
    if re.match(r"^\d+\s*[、．](?!\d)", compact):
        return BlockType.HEADING, 2, compact
    if _looks_like_code_blob(text):
        return BlockType.CODE, None, text
    if _looks_like_code_line(compact):
        return BlockType.CODE, None, text
    if re.search(r"\s{3,}", text):
        return BlockType.PARAGRAPH, None, compact
    if _looks_like_short_heading(compact):
        return BlockType.HEADING, 2, compact
    return BlockType.PARAGRAPH, None, compact


def _join_wrapped_lines(lines: list[str]) -> str:
    joined = lines[0]
    for line in lines[1:]:
        if re.search(r"[A-Za-z]-$", joined) and re.match(r"^[a-z]", line):
            joined = f"{joined[:-1]}{line}"
        elif re.search(r"[\u3400-\u9fff]$", joined) and re.match(r"^[\u3400-\u9fff]", line):
            joined = f"{joined}{line}"
        else:
            joined = f"{joined} {line}"
    return joined


def _looks_like_short_heading(text: str) -> bool:
    if not text or len(text) > 80:
        return False
    if text.endswith(("。", "！", "？", ".", "!", "?", ";", "；", ",", "，")):
        return False
    ascii_letters = [character for character in text if character.isascii() and character.isalpha()]
    if ascii_letters and all(character.isupper() for character in ascii_letters):
        return len(text.split()) <= 14
    return len(text) <= 32 and len(text.split()) <= 10


def _looks_like_code_blob(text: str) -> bool:
    if len(text) < 100:
        return False
    signals = re.findall(
        r"\b(?:class|def|elif|else|for|from|if|import|print|return|try|while|with)\b"
        r"|(?:==|!=|<=|>=|=>|->|:=|[(){}\[\]=;])",
        text,
    )
    return len(signals) >= 4


def _looks_like_code_line(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:"
            r"print\s*\(|"
            r"return(?:\s|$)|"
            r"raise(?:\s|$)|"
            r"import\s+\w|"
            r"from\s+\S+\s+import\s+\w|"
            r"(?:async\s+)?def\s+\w+\s*\(|"
            r"class\s+\w+|"
            r"if\s+.+:|"
            r"for\s+.+:|"
            r"while\s+.+:"
            r")",
            text,
        )
    )
