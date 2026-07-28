from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


def generate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cjk_font = _register_cjk_font()
    width, height = A4
    document = canvas.Canvas(str(path), pagesize=A4)
    document.setTitle("PDF Markdown Studio Visual Regression")
    document.setAuthor("PDF Markdown Studio")

    document.setFillColor(colors.HexColor("#20231F"))
    document.setFont("Helvetica-Bold", 24)
    document.drawString(54, height - 70, "PDF Markdown Studio")
    document.setFillColor(colors.HexColor("#E8552F"))
    document.setFont("Helvetica", 10)
    document.drawString(55, height - 91, "VISUAL REGRESSION DOCUMENT / PAGE 1")
    document.setFillColor(colors.HexColor("#20231F"))
    document.setFont("Helvetica", 11)
    lines = [
        "This page contains deterministic text, a structured table, and a vector chart.",
        "The converter should preserve reading order and must not silently drop any row.",
    ]
    for index, line in enumerate(lines):
        document.drawString(55, height - 125 - index * 17, line)

    table = Table(
        [
            ["Metric", "Expected", "Observed"],
            ["Page count", "2", "2"],
            ["Table rows", "4", "4"],
            ["Reading order", "Stable", "Pending conversion"],
        ],
        colWidths=[150, 130, 190],
        rowHeights=29,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E5D8")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#20231F")),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#77766F")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    table.wrapOn(document, width, height)
    table.drawOn(document, 55, height - 300)

    document.setFillColor(colors.HexColor("#F2EFE7"))
    document.roundRect(55, height - 520, 470, 145, 7, fill=1, stroke=0)
    values = [84, 61, 92, 74, 88]
    labels = ["Text", "Tables", "Images", "Order", "Output"]
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        x = 78 + index * 88
        document.setFillColor(colors.HexColor("#E8552F"))
        document.rect(x, height - 492, 30, value, fill=1, stroke=0)
        document.setFillColor(colors.HexColor("#474A44"))
        document.setFont("Helvetica", 8)
        document.drawCentredString(x + 15, height - 506, label)
        document.drawCentredString(x + 15, height - 395 + value, str(value))

    document.setFillColor(colors.HexColor("#73766E"))
    document.setFont("Helvetica", 8)
    document.drawRightString(width - 55, 35, "Source page 1 / 2")
    document.showPage()

    document.setFillColor(colors.HexColor("#20231F"))
    document.setFont(cjk_font, 22)
    document.drawString(55, height - 70, "中文结构化回归页面")
    document.setFillColor(colors.HexColor("#E8552F"))
    document.setFont("Helvetica", 10)
    document.drawString(55, height - 91, "CJK TEXT / PAGE 2")
    document.setFillColor(colors.HexColor("#20231F"))
    document.setFont(cjk_font, 12)
    chinese_lines = [
        "本页用于验证中文文本、标点符号、阅读顺序以及分页标记。",
        "解析结果应当保留这两段文字，并在质量报告中显示第二页的提取字符数。",
        "如果文本层完整，自动模式不应对整页重复执行 OCR。",
    ]
    for index, line in enumerate(chinese_lines):
        document.drawString(55, height - 135 - index * 25, line)

    cjk_table = Table(
        [
            ["项目", "预期结果"],
            ["中文文本", "完整保留"],
            ["页码", "第二页"],
            ["质量门控", "无空输出"],
        ],
        colWidths=[150, 320],
        rowHeights=34,
    )
    cjk_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E5D8")),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#77766F")),
                ("FONTNAME", (0, 0), (-1, -1), cjk_font),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    cjk_table.wrapOn(document, width, height)
    cjk_table.drawOn(document, 55, height - 370)
    document.setFillColor(colors.HexColor("#73766E"))
    document.setFont(cjk_font, 8)
    document.drawRightString(width - 55, 35, "源文件第 2 / 2 页")
    document.save()


def _register_cjk_font() -> str:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("EmbeddedCJK", str(candidate)))
            return "EmbeddedCJK"
        except Exception:
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
