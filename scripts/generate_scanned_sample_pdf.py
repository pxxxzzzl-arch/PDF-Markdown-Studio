from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PAGE_PIXELS = (1240, 1754)


def generate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", PAGE_PIXELS, "#fbfaf5")
    draw = ImageDraw.Draw(image)
    title_font = _font(54)
    heading_font = _font(30)
    body_font = _font(24)
    small_font = _font(20)

    draw.text((110, 115), "扫描件 OCR 回归样本", font=title_font, fill="#20231f")
    draw.text((112, 190), "SCANNED PDF / OCR REGRESSION", font=small_font, fill="#e8552f")
    draw.line((110, 235, 1130, 235), fill="#cbc7ba", width=2)

    lines = [
        "本页没有 PDF 文本层，所有内容都来自一张高分辨率图片。",
        "转换器应通过 OCR 保留中文、English text、数字 2026 和标点符号。",
        "Quality gate must reject silent empty output.",
    ]
    for index, line in enumerate(lines):
        draw.text((112, 300 + index * 52), line, font=body_font, fill="#30332e")

    draw.text((112, 520), "结构化表格", font=heading_font, fill="#20231f")
    left, top, right = 112, 590, 1128
    row_height = 82
    column = 470
    rows = [
        ("检查项", "预期结果"),
        ("中文 OCR", "完整保留"),
        ("英文与数字", "English 2026"),
        ("空输出检测", "不得静默通过"),
    ]
    for index, (label, value) in enumerate(rows):
        y = top + index * row_height
        if index == 0:
            draw.rectangle((left, y, right, y + row_height), fill="#e9e5d8")
        draw.rectangle((left, y, right, y + row_height), outline="#77766f", width=2)
        draw.line((column, y, column, y + row_height), fill="#77766f", width=2)
        draw.text((left + 22, y + 24), label, font=body_font, fill="#20231f")
        draw.text((column + 22, y + 24), value, font=body_font, fill="#20231f")

    draw.text((112, 1025), "阅读顺序", font=heading_font, fill="#20231f")
    draw.text((112, 1090), "第一段 -> 第二段 -> 表格 -> 页脚", font=body_font, fill="#30332e")
    draw.text(
        (112, 1160), "这是最终一行，用于确认页面末尾没有被裁切。", font=body_font, fill="#30332e"
    )
    draw.text((1000, 1660), "1 / 1", font=small_font, fill="#73766e")

    width, height = A4
    document = canvas.Canvas(str(path), pagesize=A4, pageCompression=1)
    document.setTitle("Scanned OCR Regression Sample")
    document.drawImage(ImageReader(image), 0, 0, width=width, height=height)
    document.save()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
