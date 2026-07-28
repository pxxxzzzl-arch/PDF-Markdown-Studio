from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from pdfmd.models import DocumentKind
from pdfmd.security import PdfValidationError, inspect_pdf


def test_inspect_pdf_detects_born_digital(sample_pdf: Path) -> None:
    result = inspect_pdf(sample_pdf, max_file_size=10_000_000, max_pages=10)
    assert result.page_count == 2
    assert result.kind is DocumentKind.BORN_DIGITAL
    assert result.sha256
    assert result.pages[0].native_text_chars > 100


def test_inspect_pdf_rejects_non_pdf(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(PdfValidationError, match="文件头"):
        inspect_pdf(path, max_file_size=1_000_000, max_pages=10)


def test_inspect_pdf_enforces_size(sample_pdf: Path) -> None:
    with pytest.raises(PdfValidationError, match="大小限制"):
        inspect_pdf(sample_pdf, max_file_size=10, max_pages=10)


def test_inspect_pdf_enforces_page_limit(sample_pdf: Path) -> None:
    with pytest.raises(PdfValidationError, match="页数限制"):
        inspect_pdf(sample_pdf, max_file_size=10_000_000, max_pages=1)


def test_inspect_pdf_handles_encryption(sample_pdf: Path, tmp_path: Path) -> None:
    encrypted = tmp_path / "encrypted.pdf"
    reader = PdfReader(sample_pdf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("correct-password")
    with encrypted.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(PdfValidationError, match="需要提供密码"):
        inspect_pdf(encrypted, max_file_size=10_000_000, max_pages=10)
    with pytest.raises(PdfValidationError, match="密码不正确"):
        inspect_pdf(
            encrypted,
            max_file_size=10_000_000,
            max_pages=10,
            password="wrong-password",
        )
    result = inspect_pdf(
        encrypted,
        max_file_size=10_000_000,
        max_pages=10,
        password="correct-password",
    )
    assert result.encrypted
    assert result.page_count == 2


def test_inspect_pdf_rejects_missing_and_truncated_files(tmp_path: Path) -> None:
    with pytest.raises(PdfValidationError, match="不存在"):
        inspect_pdf(tmp_path / "missing.pdf", max_file_size=1000, max_pages=10)

    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(b"%PDF-not-a-real-document")
    with pytest.raises(PdfValidationError, match="无法读取 PDF"):
        inspect_pdf(truncated, max_file_size=1000, max_pages=10)


def test_inspect_pdf_rejects_resource_bomb_page_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "huge-page.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=1_000_000_000, height=1_000_000_000)
    with path.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(PdfValidationError, match="尺寸异常"):
        inspect_pdf(path, max_file_size=1_000_000, max_pages=10)
