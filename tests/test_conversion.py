from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdfmd.config import Settings
from pdfmd.conversion import ConversionService
from pdfmd.models import (
    BlockType,
    ConversionOptions,
    DocumentBlock,
    DocumentPage,
    PageInspection,
    ParsedDocument,
    PdfInspection,
)
from pdfmd.parsers.base import ParserError
from pdfmd.parsers.docling_parser import DoclingParser
from pdfmd.parsers.native_parser import NativePdfParser


def test_native_conversion_creates_complete_result(sample_pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "result"
    settings = Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    result = ConversionService(settings).convert(
        sample_pdf,
        output,
        ConversionOptions(
            primary_engine="native",
            fallback_engine="native",
            extract_images=True,
        ),
    )

    assert (output / "document.md").is_file()
    assert (output / "document.json").is_file()
    assert (output / "quality-report.json").is_file()
    assert (output / "manifest.json").is_file()
    assert "Regression Sample" in result.markdown
    assert "<!-- page: 2 -->" not in result.markdown
    assert result.document.page_count == 2
    assert result.quality.score == 88
    assert any(issue.code == "limited_layout_validation" for issue in result.quality.issues)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == result.document.source_sha256
    assert "document.md" in manifest["files"]
    assert any(name.startswith("assets/") for name in manifest["files"])


def test_conversion_uses_original_source_filename_for_outputs(
    sample_pdf: Path, tmp_path: Path
) -> None:
    output = tmp_path / "original-name"
    result = ConversionService(
        Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    ).convert(
        sample_pdf,
        output,
        ConversionOptions(primary_engine="native", fallback_engine="native"),
        source_filename="用户上传的原始文件.pdf",
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    document = json.loads((output / "document.json").read_text(encoding="utf-8"))
    assert result.document.source_filename == "用户上传的原始文件.pdf"
    assert document["source_filename"] == "用户上传的原始文件.pdf"
    assert manifest["source_filename"] == "用户上传的原始文件.pdf"
    assert 'source: "用户上传的原始文件.pdf"' in result.markdown


def test_conversion_can_explicitly_preserve_page_markers(sample_pdf: Path, tmp_path: Path) -> None:
    result = ConversionService(
        Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    ).convert(
        sample_pdf,
        tmp_path / "page-markers",
        ConversionOptions(
            primary_engine="native",
            fallback_engine="native",
            preserve_page_markers=True,
        ),
    )
    assert "<!-- page: 1 -->" in result.markdown
    assert "<!-- page: 2 -->" in result.markdown


def test_native_conversion_preserves_chinese_text(chinese_pdf: Path, tmp_path: Path) -> None:
    result = ConversionService(
        Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    ).convert(
        chinese_pdf,
        tmp_path / "chinese-result",
        ConversionOptions(
            primary_engine="native",
            fallback_engine="native",
            extract_images=False,
        ),
    )
    assert "中文 PDF 转 Markdown 回归样本" in result.markdown
    assert "阅读顺序" in result.markdown


def test_native_conversion_recovers_numbered_code_layout(
    numbered_code_pdf: Path, tmp_path: Path
) -> None:
    result = ConversionService(
        Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    ).convert(
        numbered_code_pdf,
        tmp_path / "numbered-code-result",
        ConversionOptions(
            primary_engine="native",
            fallback_engine="native",
            extract_images=False,
        ),
    )

    code_blocks = [
        block
        for page in result.document.pages
        for block in page.blocks
        if block.type is BlockType.CODE
    ]
    assert len(code_blocks) == 1
    assert code_blocks[0].metadata["layout_recovered"] is True
    assert code_blocks[0].text.splitlines() == [
        "def greet(name):",
        "    if name:",
        '        return f"Hi {name}"',
        '    return "Hi"',
    ]
    assert " 1  " not in result.markdown


def test_reusing_output_directory_removes_stale_managed_assets(
    sample_pdf: Path, tmp_path: Path
) -> None:
    output = tmp_path / "reused-output"
    settings = Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    service = ConversionService(settings)
    service.convert(
        sample_pdf,
        output,
        ConversionOptions(primary_engine="native", fallback_engine="native", extract_images=True),
    )
    first_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    old_assets = [name for name in first_manifest["files"] if name.startswith("assets/")]
    assert old_assets
    user_file = output / "notes.txt"
    user_file.write_text("keep me", encoding="utf-8")

    service.convert(
        sample_pdf,
        output,
        ConversionOptions(
            primary_engine="native",
            fallback_engine="native",
            extract_images=False,
        ),
    )
    second_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert not any((output / name).exists() for name in old_assets)
    assert not any(name.startswith("assets/") for name in second_manifest["files"])
    assert user_file.read_text(encoding="utf-8") == "keep me"


def test_unavailable_primary_falls_back_to_native(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DoclingParser, "available", classmethod(lambda cls: False))
    output = tmp_path / "fallback"
    settings = Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    result = ConversionService(settings).convert(
        sample_pdf,
        output,
        ConversionOptions(
            primary_engine="docling",
            fallback_engine="native",
            extract_images=False,
        ),
    )
    assert result.quality.primary_engine == "native"
    assert any(issue.code == "primary_engine_unavailable" for issue in result.quality.issues)
    assert result.quality.metrics["issue_count"] == len(result.quality.issues)
    assert result.quality.metrics["warning_count"] == 1
    assert result.quality.passed is False
    assert result.quality.metrics["degraded_conversion"] is True
    assert result.quality.metrics["requested_primary_engine"] == "docling"


def test_primary_parser_failure_falls_back_to_native(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DoclingParser, "available", classmethod(lambda cls: True))

    def fail_parse(*args, **kwargs):
        raise ParserError("simulated model failure")

    monkeypatch.setattr(DoclingParser, "parse", fail_parse)
    result = ConversionService(
        Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    ).convert(
        sample_pdf,
        tmp_path / "runtime-fallback",
        ConversionOptions(
            primary_engine="docling",
            fallback_engine="native",
            extract_images=False,
        ),
    )
    assert result.quality.primary_engine == "native"
    assert any(issue.code == "primary_engine_failed" for issue in result.quality.issues)
    assert result.quality.passed is False
    assert result.quality.metrics["degraded_conversion"] is True
    assert "Regression Sample" in result.markdown


def test_transient_primary_failure_retries_once_before_fallback(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DoclingParser, "available", classmethod(lambda cls: True))
    calls = 0

    def flaky_parse(
        self,
        pdf_path,
        output_dir,
        inspection,
        options,
        *,
        pages=None,
        progress=None,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ParserError("Docling 解析失败：Broken pipe")
        document = NativePdfParser().parse(
            pdf_path,
            output_dir,
            inspection,
            options,
            pages=pages,
            progress=progress,
        )
        document.metadata["engine"] = "docling"
        for page in document.pages:
            page.engine = "docling"
            for block in page.blocks:
                block.engine = "docling"
        return document

    monkeypatch.setattr(DoclingParser, "parse", flaky_parse)
    result = ConversionService(
        Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    ).convert(
        sample_pdf,
        tmp_path / "transient-retry",
        ConversionOptions(
            primary_engine="docling",
            fallback_engine="native",
            extract_images=False,
        ),
    )

    assert calls == 2
    assert result.quality.primary_engine == "docling"
    assert not any(issue.code == "primary_engine_failed" for issue in result.quality.issues)
    assert result.quality.metrics["primary_retry_count"] == 1
    assert result.quality.metrics["degraded_conversion"] is False


def test_page_fallback_only_replaces_strictly_better_candidate(tmp_path: Path) -> None:
    inspection = PdfInspection(
        path="sample.pdf",
        filename="sample.pdf",
        sha256="f" * 64,
        file_size=100,
        page_count=1,
        pages=[PageInspection(number=1, native_text_chars=100)],
    )
    primary = _document_with_text("short", engine="docling")
    better = _document_with_text("complete " * 20, engine="paddleocr")
    merged, applied = ConversionService._select_better_pages(
        primary,
        better,
        inspection,
        tmp_path,
        72,
    )
    assert applied == [1]
    assert merged.pages[0].engine == "paddleocr"

    equal_primary = _document_with_text("complete " * 20, engine="docling")
    equal_fallback = _document_with_text("alternative " * 20, engine="paddleocr")
    merged, applied = ConversionService._select_better_pages(
        equal_primary,
        equal_fallback,
        inspection,
        tmp_path,
        72,
    )
    assert applied == []
    assert merged.pages[0].engine == "docling"


def _document_with_text(text: str, *, engine: str) -> ParsedDocument:
    return ParsedDocument(
        source_filename="sample.pdf",
        source_sha256="f" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine=engine,
                blocks=[
                    DocumentBlock(
                        id=f"{engine}-text",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text=text,
                        engine=engine,
                    )
                ],
            )
        ],
        metadata={"engine": engine},
    )
