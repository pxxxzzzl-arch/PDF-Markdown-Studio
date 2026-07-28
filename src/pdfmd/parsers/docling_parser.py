from __future__ import annotations

import importlib.util
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pdfmd.layout_recovery import recover_code_layout
from pdfmd.models import (
    BlockType,
    BoundingBox,
    ConversionOptions,
    DocumentBlock,
    DocumentPage,
    ParsedDocument,
    PdfInspection,
)
from pdfmd.parsers.base import BaseParser, ParserError, ProgressCallback
from pdfmd.table_utils import is_valid_gfm_table


class DoclingParser(BaseParser):
    name = "docling"

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("docling") is not None

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
            raise ParserError("Docling 未安装，请安装 primary 可选依赖")
        if progress:
            progress(5, "加载 Docling 模型")

        try:
            converter = self._make_converter(options)
            result = converter.convert(str(pdf_path))
            doc = result.document
        except Exception as exc:
            raise ParserError(f"Docling 解析失败：{exc}") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        page_filter = set(pages) if pages else None
        blocks_by_page: dict[int, list[DocumentBlock]] = {
            page.number: []
            for page in inspection.pages
            if page_filter is None or page.number in page_filter
        }

        try:
            iterator = doc.iterate_items()
        except Exception as exc:
            raise ParserError(f"Docling 文档结构不可遍历：{exc}") from exc

        counters: dict[int, int] = {}
        for item, tree_level in iterator:
            page_number, bbox = _provenance(item)
            if page_number is None or page_number not in blocks_by_page:
                continue
            counters[page_number] = counters.get(page_number, 0) + 1
            block = self._map_item(
                doc,
                item,
                page_number,
                bbox,
                tree_level,
                counters[page_number],
                assets_dir,
                options,
            )
            if block:
                blocks_by_page[page_number].append(block)

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
                # Structural extraction remains usable when a PDF text layer is
                # malformed; quality gates will still flag collapsed code below.
                layout_recovery = {"error": exc.__class__.__name__}

        # A raw Markdown block is safer than returning empty output when an upstream schema changes.
        if not any(blocks_by_page.values()):
            try:
                markdown = doc.export_to_markdown()
            except Exception as exc:
                raise ParserError(f"Docling 未返回可用内容：{exc}") from exc
            blocks_by_page[min(blocks_by_page or {1: []})].append(
                DocumentBlock(
                    id="docling-raw-markdown",
                    type=BlockType.RAW_MARKDOWN,
                    page=min(blocks_by_page or {1: []}),
                    text=markdown,
                    engine=self.name,
                    metadata={"fallback_reason": "unknown_docling_schema"},
                )
            )

        document_pages: list[DocumentPage] = []
        for page_info in inspection.pages:
            if page_info.number not in blocks_by_page:
                continue
            document_pages.append(
                DocumentPage(
                    number=page_info.number,
                    width=page_info.width,
                    height=page_info.height,
                    blocks=blocks_by_page[page_info.number],
                    engine=self.name,
                    source_text_chars=page_info.native_text_chars,
                    source_image_count=page_info.image_count,
                )
            )
        if progress:
            progress(100, "Docling 结构化解析完成")

        document_metadata: dict[str, Any] = {
            "engine": self.name,
            "author": inspection.author,
            "layout_recovery": layout_recovery,
        }
        confidence = _serialize_docling_confidence(result)
        if confidence is not None:
            document_metadata["docling_confidence"] = confidence

        return ParsedDocument(
            source_filename=inspection.filename,
            source_sha256=inspection.sha256,
            title=inspection.title,
            page_count=inspection.page_count,
            kind=inspection.kind,
            pages=document_pages,
            metadata=document_metadata,
        )

    @staticmethod
    def _make_converter(options: ConversionOptions) -> Any:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        try:
            from docling.datamodel.pipeline_options import HeadingHierarchyOptions
        except ImportError:  # Docling releases before hierarchy inference existed.
            HeadingHierarchyOptions = None  # type: ignore[assignment,misc]

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = True
        pipeline_options.do_ocr = options.ocr_mode.value != "never"
        _configure_code_enrichment(pipeline_options, options.enable_code_enrichment)
        # Docling leaves every detected PDF heading at the same level unless this
        # pass is enabled.  Keeping parsed pages is required for the font-style
        # fallback used by the hierarchy model.
        if hasattr(pipeline_options, "generate_parsed_pages"):
            pipeline_options.generate_parsed_pages = True
        if HeadingHierarchyOptions is not None and hasattr(
            pipeline_options, "heading_hierarchy_options"
        ):
            pipeline_options.heading_hierarchy_options = HeadingHierarchyOptions(
                enabled=True,
                use_bookmarks=True,
                use_numbering=True,
                use_style=True,
                max_level=6,
            )
        if hasattr(pipeline_options, "generate_picture_images"):
            pipeline_options.generate_picture_images = options.extract_images
        if hasattr(pipeline_options, "generate_page_images"):
            pipeline_options.generate_page_images = False
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )

    def _map_item(
        self,
        doc: Any,
        item: Any,
        page_number: int,
        bbox: BoundingBox | None,
        tree_level: int,
        sequence: int,
        assets_dir: Path,
        options: ConversionOptions,
    ) -> DocumentBlock | None:
        label = str(getattr(item, "label", "text")).lower().split(".")[-1]
        block_type = _block_type(label)
        raw_text = str(getattr(item, "text", "") or "")
        # Leading whitespace is semantic in source code. Docling may surround a
        # CodeItem with blank lines, but ordinary ``strip()`` also destroys the
        # indentation needed to reconstruct a valid fenced code block.
        text = raw_text.strip("\r\n") if block_type is BlockType.CODE else raw_text.strip()
        block_id = f"p{page_number}-docling-{sequence}"
        common = {
            "id": block_id,
            "page": page_number,
            "bbox": bbox,
            "engine": self.name,
            "metadata": _item_metadata(item, label, tree_level),
        }

        if block_type is BlockType.TABLE:
            html = None
            markdown = text
            try:
                html = item.export_to_html(doc=doc)
            except Exception:
                try:
                    html = item.export_to_html()
                except Exception:
                    pass
            metadata = common["metadata"]
            if not bool(metadata.get("has_spans")):
                try:
                    markdown = item.export_to_markdown(doc=doc)
                except Exception:
                    try:
                        markdown = item.export_to_markdown()
                    except Exception:
                        pass
            has_spans = bool(metadata.get("has_spans"))
            expected_rows = _positive_int(metadata.get("table_rows"))
            expected_columns = _positive_int(metadata.get("table_columns"))
            markdown_valid = bool(markdown) and is_valid_gfm_table(
                markdown,
                expected_rows=expected_rows,
                expected_columns=expected_columns,
            )
            use_html = has_spans or not markdown_valid
            metadata["table_serialization"] = "html" if use_html and html else "gfm"
            if markdown and not markdown_valid:
                metadata["gfm_validation_failed"] = True
            return DocumentBlock(
                type=block_type,
                text=markdown,
                table_html=html if use_html else None,
                **common,
            )

        if block_type is BlockType.IMAGE:
            asset_path = None
            is_tiny = _is_tiny_picture(bbox)
            common["metadata"]["suppressed_tiny_picture"] = is_tiny
            if options.extract_images and not is_tiny:
                try:
                    image = item.get_image(doc)
                    safe_name = f"page-{page_number:04d}-picture-{sequence:03d}.png"
                    image.save(assets_dir / safe_name, format="PNG")
                    asset_path = f"assets/{safe_name}"
                except Exception as exc:
                    common["metadata"]["asset_extraction_error"] = exc.__class__.__name__
            return DocumentBlock(
                type=block_type,
                text=text or f"第 {page_number} 页图片",
                asset_path=asset_path,
                **common,
            )

        if not text:
            return None
        level = None
        if block_type is BlockType.HEADING:
            # SectionHeaderItem.level is the semantic level.  Level 1 sits below
            # the document title, hence the +1 Markdown level.  tree_level is a
            # traversal depth and must not be used as a heading level.
            semantic_level = _safe_int(getattr(item, "level", None), default=1)
            level = min(6, max(2, semantic_level + 1))
        return DocumentBlock(type=block_type, text=text, level=level, **common)


def _item_metadata(item: Any, label: str, tree_level: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "docling_label": label,
        "tree_level": tree_level,
    }
    self_ref = getattr(item, "self_ref", None)
    if self_ref:
        metadata["self_ref"] = str(self_ref)
    parent = getattr(item, "parent", None)
    parent_ref = getattr(parent, "cref", None) or getattr(parent, "ref", None)
    if parent_ref:
        metadata["parent_ref"] = str(parent_ref)
    content_layer = getattr(item, "content_layer", None)
    if content_layer is not None:
        metadata["content_layer"] = _enum_value(content_layer)

    formatting = getattr(item, "formatting", None)
    if formatting is not None:
        try:
            metadata["formatting"] = formatting.model_dump(mode="json", exclude_none=True)
        except Exception:
            metadata["formatting"] = str(formatting)
    hyperlink = getattr(item, "hyperlink", None)
    if hyperlink:
        metadata["hyperlink"] = str(hyperlink)

    if label == "section_header":
        metadata["semantic_level"] = _safe_int(getattr(item, "level", None), default=1)
    if label == "list_item":
        metadata["ordered"] = bool(getattr(item, "enumerated", False))
        marker = str(getattr(item, "marker", "-") or "-").strip()
        metadata["marker"] = marker
        metadata["indent_level"] = max(0, tree_level - 2)
    if label == "code":
        language = _enum_value(getattr(item, "code_language", ""))
        if language and language != "unknown":
            metadata["language"] = language.lower()
            metadata["language_source"] = "docling"
    if label == "table":
        table_data = getattr(item, "data", None)
        cells = list(getattr(table_data, "table_cells", None) or [])
        metadata["table_rows"] = _safe_int(getattr(table_data, "num_rows", None), default=0)
        metadata["table_columns"] = _safe_int(getattr(table_data, "num_cols", None), default=0)
        metadata["table_cells"] = len(cells)
        metadata["has_spans"] = any(
            _safe_int(getattr(cell, "row_span", None), default=1) > 1
            or _safe_int(getattr(cell, "col_span", None), default=1) > 1
            for cell in cells
        )
    return metadata


def _is_tiny_picture(bbox: BoundingBox | None) -> bool:
    if bbox is None:
        return False
    width = max(0.0, bbox.right - bbox.left)
    height = max(0.0, bbox.top - bbox.bottom)
    return width < 24 or height < 18 or width * height < 500


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any) -> int | None:
    parsed = _safe_int(value, default=0)
    return parsed if parsed > 0 else None


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _configure_code_enrichment(pipeline_options: Any, enabled: bool) -> None:
    """Enable Docling's optional code model without breaking older releases."""

    if hasattr(pipeline_options, "do_code_enrichment"):
        pipeline_options.do_code_enrichment = enabled


def _serialize_docling_confidence(result: Any) -> Any | None:
    """Return a JSON-safe snapshot across Docling confidence schema versions."""

    try:
        confidence = getattr(result, "confidence", None)
    except Exception:
        return None
    if confidence is None:
        return None

    value = confidence
    try:
        model_dump = getattr(confidence, "model_dump", None)
    except Exception:
        model_dump = None
    if callable(model_dump):
        dump_options = (
            {
                "mode": "json",
                "exclude_none": True,
                "exclude_computed_fields": True,
            },
            {"mode": "json", "exclude_none": True},
            {"exclude_none": True},
            {},
        )
        for kwargs in dump_options:
            try:
                value = model_dump(**kwargs)
                break
            except TypeError:
                continue
            except Exception:
                break
    else:
        try:
            legacy_dump = getattr(confidence, "dict", None)
        except Exception:
            legacy_dump = None
        if callable(legacy_dump):
            try:
                value = legacy_dump(exclude_none=True)
            except Exception:
                value = confidence

    return _json_safe_metadata(value)


def _json_safe_metadata(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe_metadata(item) for item in value]

    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _json_safe_metadata(enum_value)
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            item = item_method()
        except Exception:
            pass
        else:
            if item is not value:
                return _json_safe_metadata(item)
    try:
        attributes = vars(value)
    except (TypeError, ValueError):
        attributes = None
    if attributes:
        return {
            str(key): _json_safe_metadata(item)
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _provenance(item: Any) -> tuple[int | None, BoundingBox | None]:
    provenance = getattr(item, "prov", None) or []
    if not provenance:
        return None, None
    prov = provenance[0]
    page_number = int(getattr(prov, "page_no", 0) or 0)
    if page_number == 0:
        return None, None
    raw_bbox = getattr(prov, "bbox", None)
    if raw_bbox is None:
        return page_number, None
    try:
        return page_number, BoundingBox(
            left=float(raw_bbox.l),
            top=float(raw_bbox.t),
            right=float(raw_bbox.r),
            bottom=float(raw_bbox.b),
        )
    except (TypeError, ValueError, AttributeError):
        return page_number, None


def _block_type(label: str) -> BlockType:
    mapping = {
        "title": BlockType.TITLE,
        "section_header": BlockType.HEADING,
        "text": BlockType.PARAGRAPH,
        "paragraph": BlockType.PARAGRAPH,
        "list_item": BlockType.LIST_ITEM,
        "table": BlockType.TABLE,
        "formula": BlockType.FORMULA,
        "code": BlockType.CODE,
        "picture": BlockType.IMAGE,
        "caption": BlockType.CAPTION,
        "footnote": BlockType.FOOTNOTE,
        "page_header": BlockType.PAGE_HEADER,
        "page_footer": BlockType.PAGE_FOOTER,
    }
    return mapping.get(label, BlockType.PARAGRAPH)
