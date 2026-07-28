from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from pdfmd.models import ConversionOptions, ParsedDocument, PdfInspection

ProgressCallback = Callable[[int, str], None]


class ParserError(RuntimeError):
    """Raised when a parsing engine cannot complete a conversion."""


class BaseParser(ABC):
    name: str

    @classmethod
    @abstractmethod
    def available(cls) -> bool: ...

    @abstractmethod
    def parse(
        self,
        pdf_path: Path,
        output_dir: Path,
        inspection: PdfInspection,
        options: ConversionOptions,
        *,
        pages: list[int] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParsedDocument: ...
