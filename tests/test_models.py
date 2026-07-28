from __future__ import annotations

import pytest
from pydantic import ValidationError

from pdfmd.models import ConversionOptions, DocumentPage, ParsedDocument


def test_conversion_options_normalize_engine_names_and_hide_password() -> None:
    options = ConversionOptions(
        primary_engine=" DOCLING ",
        fallback_engine=" PaddleOCR ",
        password="secret",
    )
    assert options.primary_engine == "docling"
    assert options.fallback_engine == "paddleocr"
    assert options.enable_code_enrichment is False
    assert options.preserve_page_markers is False
    assert options.password == "secret"
    assert "password" not in options.model_dump()
    assert "secret" not in options.model_dump_json()


def test_conversion_options_can_disable_code_enrichment() -> None:
    options = ConversionOptions(enable_code_enrichment=False)
    assert options.enable_code_enrichment is False


@pytest.mark.parametrize("field", ["primary_engine", "fallback_engine"])
def test_conversion_options_reject_unknown_engines(field: str) -> None:
    with pytest.raises(ValidationError, match="未知解析引擎"):
        ConversionOptions.model_validate({field: "shell-command"})


def test_parsed_document_sorts_pages_and_rejects_duplicates() -> None:
    document = ParsedDocument(
        source_filename="sample.pdf",
        source_sha256="a" * 64,
        page_count=2,
        pages=[
            DocumentPage(number=2, engine="test"),
            DocumentPage(number=1, engine="test"),
        ],
    )
    assert [page.number for page in document.pages] == [1, 2]

    with pytest.raises(ValidationError, match="page numbers must be unique"):
        ParsedDocument(
            source_filename="sample.pdf",
            source_sha256="a" * 64,
            page_count=2,
            pages=[
                DocumentPage(number=1, engine="test"),
                DocumentPage(number=1, engine="test"),
            ],
        )
