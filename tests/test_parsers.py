from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from pdfmd.models import (
    BlockType,
    BoundingBox,
    ConversionOptions,
    DocumentKind,
    PageInspection,
    PdfInspection,
)
from pdfmd.parsers.base import ParserError
from pdfmd.parsers.docling_parser import (
    DoclingParser,
    _block_type,
    _configure_code_enrichment,
)
from pdfmd.parsers.native_parser import _classify_paragraph
from pdfmd.parsers.paddle_parser import PaddleOcrParser
from pdfmd.security import inspect_pdf


def test_docling_label_mapping_covers_structural_blocks() -> None:
    assert _block_type("section_header") is BlockType.HEADING
    assert _block_type("table") is BlockType.TABLE
    assert _block_type("picture") is BlockType.IMAGE
    assert _block_type("formula") is BlockType.FORMULA
    assert _block_type("new-upstream-label") is BlockType.PARAGRAPH


def test_native_short_code_line_is_not_promoted_to_heading() -> None:
    block_type, level, text = _classify_paragraph(
        "print(fibonacci(10))  # expected: 55",
        page_number=1,
        block_index=4,
    )

    assert block_type is BlockType.CODE
    assert level is None
    assert text == "print(fibonacci(10))  # expected: 55"


def test_docling_mapping_preserves_semantic_heading_list_and_code_metadata(
    tmp_path: Path,
) -> None:
    parser = DoclingParser()
    assets = tmp_path / "assets"
    assets.mkdir()

    heading = parser._map_item(
        None,
        SimpleNamespace(
            label="section_header",
            text="1.2.3 Deep section",
            level=3,
            self_ref="#/texts/1",
        ),
        1,
        None,
        1,
        1,
        assets,
        ConversionOptions(),
    )
    assert heading is not None
    assert heading.level == 4
    assert heading.metadata["semantic_level"] == 3

    list_item = parser._map_item(
        None,
        SimpleNamespace(
            label="list_item",
            text="Second item",
            enumerated=True,
            marker="2.",
        ),
        1,
        None,
        4,
        2,
        assets,
        ConversionOptions(),
    )
    assert list_item is not None
    assert list_item.metadata["ordered"] is True
    assert list_item.metadata["marker"] == "2."
    assert list_item.metadata["indent_level"] == 2

    code = parser._map_item(
        None,
        SimpleNamespace(
            label="code",
            text="\n    def answer():\n        return 42\n\n",
            code_language=SimpleNamespace(value="python"),
        ),
        1,
        BoundingBox(left=10, top=100, right=200, bottom=50),
        1,
        3,
        assets,
        ConversionOptions(),
    )
    assert code is not None
    assert code.text == "    def answer():\n        return 42"
    assert code.metadata["language"] == "python"
    assert code.metadata["language_source"] == "docling"


def test_docling_code_enrichment_configuration_is_version_safe() -> None:
    supported = SimpleNamespace(do_code_enrichment=False)
    _configure_code_enrichment(supported, True)
    assert supported.do_code_enrichment is True

    unsupported = SimpleNamespace()
    _configure_code_enrichment(unsupported, True)
    assert not hasattr(unsupported, "do_code_enrichment")


def test_docling_parse_serializes_result_confidence_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeDocument:
        @staticmethod
        def iterate_items():
            return iter(())

        @staticmethod
        def export_to_markdown():
            return "# Fallback"

    result = SimpleNamespace(
        document=FakeDocument(),
        confidence={
            "layout_score": 0.91,
            "parse_score": math.nan,
            "pages": {1: {"ocr_score": 0.87}},
        },
    )
    converter = SimpleNamespace(convert=lambda _path: result)
    parser = DoclingParser()
    monkeypatch.setattr(parser, "available", lambda: True)
    monkeypatch.setattr(parser, "_make_converter", lambda _options: converter)
    inspection = PdfInspection(
        path=str(tmp_path / "input.pdf"),
        filename="input.pdf",
        sha256="a" * 64,
        file_size=1,
        page_count=1,
        kind=DocumentKind.UNKNOWN,
        pages=[PageInspection(number=1)],
    )

    document = parser.parse(
        tmp_path / "input.pdf",
        tmp_path / "output",
        inspection,
        ConversionOptions(),
    )

    confidence = document.metadata["docling_confidence"]
    assert confidence["layout_score"] == 0.91
    assert confidence["parse_score"] is None
    assert confidence["pages"]["1"]["ocr_score"] == 0.87
    assert "NaN" not in document.model_dump_json()


def test_docling_table_falls_back_to_html_when_gfm_columns_are_broken(tmp_path: Path) -> None:
    parser = DoclingParser()
    assets = tmp_path / "assets"
    assets.mkdir()
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>y | z</td></tr></table>"
    broken_markdown = "| A | B |\n| --- | --- |\n| x | y | z |"
    item = SimpleNamespace(
        label="table",
        text="",
        data=SimpleNamespace(num_rows=2, num_cols=2, table_cells=[]),
        export_to_html=lambda doc=None: html,
        export_to_markdown=lambda doc=None: broken_markdown,
    )

    table = parser._map_item(
        None,
        item,
        1,
        None,
        1,
        1,
        assets,
        ConversionOptions(),
    )

    assert table is not None
    assert table.table_html == html
    assert table.metadata["gfm_validation_failed"] is True
    assert table.metadata["table_serialization"] == "html"


def test_docling_table_keeps_valid_gfm_serialization(tmp_path: Path) -> None:
    parser = DoclingParser()
    assets = tmp_path / "assets"
    assets.mkdir()
    markdown = "| A | B |\n| --- | --- |\n| x | y |"
    item = SimpleNamespace(
        label="table",
        text="",
        data=SimpleNamespace(num_rows=2, num_cols=2, table_cells=[]),
        export_to_html=lambda doc=None: "<table></table>",
        export_to_markdown=lambda doc=None: markdown,
    )

    table = parser._map_item(
        None,
        item,
        1,
        None,
        1,
        1,
        assets,
        ConversionOptions(),
    )

    assert table is not None
    assert table.text == markdown
    assert table.table_html is None
    assert table.metadata["table_serialization"] == "gfm"


def test_paddle_availability_requires_runtime_and_package(monkeypatch) -> None:
    real_find_spec = importlib.util.find_spec

    def only_ocr(name: str):
        if name == "paddleocr":
            return object()
        if name == "paddle":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", only_ocr)
    assert not PaddleOcrParser.available()

    def full_runtime(name: str):
        if name in {"paddleocr", "paddle"}:
            return object()
        return real_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", full_runtime)
    assert PaddleOcrParser.available()


def test_paddle_subset_writer_keeps_requested_page(sample_pdf: Path, tmp_path: Path) -> None:
    destination = tmp_path / "subset.pdf"
    PaddleOcrParser._write_subset(sample_pdf, destination, [2], None)
    reader = PdfReader(destination)
    assert len(reader.pages) == 1
    assert "Second Page" in (reader.pages[0].extract_text() or "")


def test_paddle_removes_subset_when_pipeline_initialization_fails(
    sample_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    class BrokenPipeline:
        def __init__(self) -> None:
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(PaddleOcrParser, "available", classmethod(lambda cls: True))
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=BrokenPipeline))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    inspection = inspect_pdf(
        sample_pdf,
        max_file_size=10_000_000,
        max_pages=10,
    )

    with pytest.raises(ParserError, match="PaddleOCR 解析失败"):
        PaddleOcrParser().parse(
            sample_pdf,
            output_dir,
            inspection,
            ConversionOptions(),
            pages=[1],
        )

    assert not (output_dir / ".fallback-pages.pdf").exists()
