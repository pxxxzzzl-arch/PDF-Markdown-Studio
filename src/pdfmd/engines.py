from __future__ import annotations

from pdfmd.models import EngineStatus
from pdfmd.parsers import BaseParser, DoclingParser, NativePdfParser, PaddleOcrParser

PARSER_TYPES: dict[str, type[BaseParser]] = {
    "docling": DoclingParser,
    "native": NativePdfParser,
    "paddleocr": PaddleOcrParser,
}


def get_parser(name: str) -> BaseParser:
    normalized = name.strip().lower()
    try:
        parser_type = PARSER_TYPES[normalized]
    except KeyError as exc:
        raise ValueError(f"未知解析引擎：{name}") from exc
    return parser_type()


def engine_statuses() -> list[EngineStatus]:
    details = {
        "docling": ("主解析", "版面、阅读顺序、表格和公式"),
        "paddleocr": ("OCR 兜底", "中文扫描件和复杂版式"),
        "native": ("轻量兜底", "仅适合带文本层的普通 PDF"),
    }
    return [
        EngineStatus(
            name=name,
            available=parser_type.available(),
            role=details[name][0],
            detail=details[name][1],
        )
        for name, parser_type in PARSER_TYPES.items()
    ]
