from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pdfmd import __version__
from pdfmd.config import Settings
from pdfmd.conversion import ConversionService
from pdfmd.engines import engine_statuses
from pdfmd.models import ConversionOptions, OcrMode
from pdfmd.parsers.base import ParserError
from pdfmd.security import PdfValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfmd",
        description="将 PDF 转换为 Markdown，并生成结构化数据和质量报告",
    )
    parser.add_argument("input", nargs="?", type=Path, help="输入 PDF")
    parser.add_argument("-o", "--output", type=Path, default=Path("output"))
    parser.add_argument("--engine", default="docling", choices=["docling", "native", "paddleocr"])
    parser.add_argument("--fallback-engine", default="paddleocr", choices=["paddleocr", "native"])
    parser.add_argument("--ocr", default="auto", choices=[mode.value for mode in OcrMode])
    parser.add_argument(
        "--code-enrichment",
        action="store_true",
        help="启用 Docling 视觉代码增强（实验性；原生文字课件建议关闭）",
    )
    parser.add_argument("--password")
    parser.add_argument("--no-images", action="store_true")
    page_markers = parser.add_mutually_exclusive_group()
    page_markers.add_argument(
        "--page-markers",
        dest="preserve_page_markers",
        action="store_true",
        help="在 Markdown 中保留分页注释（默认关闭）",
    )
    page_markers.add_argument(
        "--no-page-markers",
        dest="preserve_page_markers",
        action="store_false",
        help="不输出分页注释（兼容旧版参数，现为默认行为）",
    )
    parser.set_defaults(preserve_page_markers=False)
    parser.add_argument("--no-quality-fallback", action="store_true")
    parser.add_argument(
        "--minimum-quality-score",
        type=_quality_score,
        default=72,
        metavar="0-100",
    )
    parser.add_argument("--engine-status", action="store_true")
    parser.add_argument("--debug", action="store_true", help="失败时显示完整异常堆栈")
    parser.add_argument("--version", action="version", version=f"PDF Markdown Studio {__version__}")
    return parser


def _quality_score(value: str) -> float:
    try:
        score = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("质量分必须是 0 到 100 的数字") from exc
    if not 0 <= score <= 100:
        raise argparse.ArgumentTypeError("质量分必须在 0 到 100 之间")
    return score


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.engine_status:
        statuses = [item.model_dump() for item in engine_statuses()]
        print(json.dumps(statuses, ensure_ascii=False, indent=2))
        return 0
    if not args.input:
        build_parser().error("需要提供输入 PDF")

    settings = Settings.from_env()
    options = ConversionOptions(
        primary_engine=args.engine,
        fallback_engine=args.fallback_engine,
        ocr_mode=OcrMode(args.ocr),
        enable_code_enrichment=args.code_enrichment,
        password=args.password,
        extract_images=not args.no_images,
        preserve_page_markers=args.preserve_page_markers,
        enable_quality_fallback=not args.no_quality_fallback,
        minimum_quality_score=args.minimum_quality_score,
    )
    service = ConversionService(settings)

    def progress(value: int, stage: str) -> None:
        print(f"[{value:3d}%] {stage}")

    try:
        result = service.convert(args.input, args.output, options, progress=progress)
    except (PdfValidationError, ParserError, OSError, ValueError) as exc:
        if args.debug:
            raise
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"Markdown: {result.markdown_path}")
    print(f"质量评分: {result.quality.score:.1f}")
    return 0 if result.quality.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
