from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from pdfmd.models import (
    BlockType,
    ConversionOptions,
    DocumentBlock,
    DocumentPage,
    ParsedDocument,
    PdfInspection,
)
from pdfmd.parsers.base import BaseParser, ParserError, ProgressCallback


class PaddleOcrParser(BaseParser):
    name = "paddleocr"

    @classmethod
    def available(cls) -> bool:
        return (
            importlib.util.find_spec("paddleocr") is not None
            and importlib.util.find_spec("paddle") is not None
        )

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
        if not self.available():
            raise ParserError("PaddleOCR 未安装，跳过 OCR 兜底")
        selected = pages or list(range(1, inspection.page_count + 1))
        work_pdf = pdf_path
        page_map = selected
        if pages:
            work_pdf = output_dir / ".fallback-pages.pdf"
            self._write_subset(pdf_path, work_pdf, selected, options.password)

        try:
            try:
                from paddleocr import PPStructureV3

                pipeline = PPStructureV3()
                results = pipeline.predict(input=str(work_pdf))
            except Exception as exc:
                raise ParserError(f"PaddleOCR 解析失败：{exc}") from exc

            assets_dir = output_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            document_pages: list[DocumentPage] = []
            for result_index, result in enumerate(results):
                if result_index >= len(page_map):
                    break
                page_number = page_map[result_index]
                markdown_info: dict[str, Any] = getattr(result, "markdown", {}) or {}
                markdown_text = markdown_info.get("markdown_texts", "")
                if isinstance(markdown_text, list):
                    markdown_text = "\n\n".join(map(str, markdown_text))
                markdown_text = str(markdown_text or "").strip()

                for image_index, (name, image) in enumerate(
                    (markdown_info.get("markdown_images") or {}).items(), start=1
                ):
                    suffix = Path(name).suffix or ".png"
                    safe_name = f"page-{page_number:04d}-ocr-{image_index:03d}{suffix}"
                    try:
                        image.save(assets_dir / safe_name)
                    except Exception:
                        continue
                    markdown_text = markdown_text.replace(str(name), f"assets/{safe_name}")

                page_info = inspection.pages[page_number - 1]
                document_pages.append(
                    DocumentPage(
                        number=page_number,
                        width=page_info.width,
                        height=page_info.height,
                        blocks=[
                            DocumentBlock(
                                id=f"p{page_number}-paddle-markdown",
                                type=BlockType.RAW_MARKDOWN,
                                page=page_number,
                                text=markdown_text,
                                engine=self.name,
                            )
                        ],
                        engine=self.name,
                        source_text_chars=page_info.native_text_chars,
                        source_image_count=page_info.image_count,
                    )
                )
                if progress:
                    progress(
                        int((result_index + 1) / len(selected) * 100),
                        f"OCR 处理第 {page_number} 页",
                    )

            return ParsedDocument(
                source_filename=inspection.filename,
                source_sha256=inspection.sha256,
                title=inspection.title,
                page_count=inspection.page_count,
                kind=inspection.kind,
                pages=document_pages,
                metadata={"engine": self.name, "fallback_pages": selected},
            )
        finally:
            if pages and work_pdf.exists():
                work_pdf.unlink(missing_ok=True)

    @staticmethod
    def _write_subset(
        source: Path, destination: Path, pages: list[int], password: str | None
    ) -> None:
        reader = PdfReader(str(source), strict=False)
        if reader.is_encrypted and password:
            reader.decrypt(password)
        writer = PdfWriter()
        for number in pages:
            writer.add_page(reader.pages[number - 1])
        with destination.open("wb") as stream:
            writer.write(stream)
