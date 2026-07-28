from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from pdfmd.models import DocumentKind, PageInspection, PdfInspection


class PdfValidationError(ValueError):
    """Raised when an input file is unsafe or unsupported."""


MAX_PAGE_DIMENSION = 20_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(
    path: Path,
    *,
    max_file_size: int,
    max_pages: int,
    password: str | None = None,
) -> PdfInspection:
    path = path.resolve()
    if not path.is_file():
        raise PdfValidationError("PDF 文件不存在")

    file_size = path.stat().st_size
    if file_size == 0:
        raise PdfValidationError("PDF 文件为空")
    if file_size > max_file_size:
        raise PdfValidationError(f"PDF 超过大小限制（{max_file_size // 1024 // 1024} MB）")

    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise PdfValidationError("文件头不是有效的 PDF")

    try:
        reader = PdfReader(str(path), strict=False)
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            if not password:
                raise PdfValidationError("PDF 已加密，需要提供密码")
            if not reader.decrypt(password):
                raise PdfValidationError("PDF 密码不正确")

        page_count = len(reader.pages)
        if page_count == 0:
            raise PdfValidationError("PDF 没有页面")
        if page_count > max_pages:
            raise PdfValidationError(f"PDF 超过页数限制（{max_pages} 页）")

        pages: list[PageInspection] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            native_chars = len("".join(text.split()))
            try:
                image_count = len(page.images)
            except Exception:
                image_count = 0
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
            if (
                width <= 0
                or height <= 0
                or width > MAX_PAGE_DIMENSION
                or height > MAX_PAGE_DIMENSION
            ):
                raise PdfValidationError(f"第 {index} 页尺寸异常（{width:g} × {height:g} pt）")
            pages.append(
                PageInspection(
                    number=index,
                    width=width,
                    height=height,
                    rotation=int(page.get("/Rotate", 0) or 0) % 360,
                    native_text_chars=native_chars,
                    image_count=image_count,
                    is_blank=native_chars == 0 and image_count == 0,
                )
            )
    except PdfValidationError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfValidationError(f"无法读取 PDF：{exc}") from exc

    text_pages = sum(page.native_text_chars >= 20 for page in pages)
    image_only_pages = sum(page.native_text_chars < 20 and page.image_count > 0 for page in pages)
    if text_pages == page_count:
        kind = DocumentKind.BORN_DIGITAL
    elif image_only_pages >= max(1, int(page_count * 0.8)):
        kind = DocumentKind.SCANNED
    elif text_pages or image_only_pages:
        kind = DocumentKind.MIXED
    else:
        kind = DocumentKind.UNKNOWN

    metadata = reader.metadata or {}
    return PdfInspection(
        path=str(path),
        filename=path.name,
        sha256=_sha256(path),
        file_size=file_size,
        page_count=page_count,
        encrypted=encrypted,
        kind=kind,
        title=_clean_metadata(metadata.get("/Title")),
        author=_clean_metadata(metadata.get("/Author")),
        pages=pages,
    )


def _clean_metadata(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\x00", "")
    return text or None
