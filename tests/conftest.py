from __future__ import annotations

import io
from pathlib import Path

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(str(path), pagesize=A4, title="Quality Gate Sample")
    story = [
        Paragraph("PDF Markdown Studio - Regression Sample", styles["Title"]),
        Spacer(1, 14),
        Paragraph(
            "This born-digital page contains headings, paragraphs, a table, and an embedded image. "
            "It is intentionally deterministic so extraction regressions are easy to detect.",
            styles["BodyText"],
        ),
        Spacer(1, 16),
        Table(
            [
                ["Metric", "Target", "Result"],
                ["Text coverage", "> 90%", "Pass"],
                ["Pages", "2", "2"],
            ],
            colWidths=[150, 100, 100],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E5D8")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#444444")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("PADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        ),
        Spacer(1, 20),
        _sample_image(),
        PageBreak(),
        Paragraph("Second Page", styles["Heading1"]),
        Paragraph(
            "The second page verifies page ordering and page markers. "
            "A converter must not silently drop this sentence.",
            styles["BodyText"],
        ),
    ]
    document.build(story)
    return path


@pytest.fixture()
def chinese_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "chinese.pdf"
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    styles["Title"].fontName = "STSong-Light"
    styles["BodyText"].fontName = "STSong-Light"
    document = SimpleDocTemplate(str(path), pagesize=A4, title="中文回归样本")
    document.build(
        [
            Paragraph("中文 PDF 转 Markdown 回归样本", styles["Title"]),
            Spacer(1, 12),
            Paragraph("这一段用于检查中文文本提取、阅读顺序以及元数据编码。", styles["BodyText"]),
        ]
    )
    return path


@pytest.fixture()
def numbered_code_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "numbered-code.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle("Numbered Code Layout")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, 790, "Numbered Code Layout")
    code = pdf.beginText(72, 735)
    code.setFont("Courier", 10)
    code.setLeading(12)
    for line in (
        " 1  def greet(name):",
        " 2      if name:",
        ' 3          return f"Hi {name}"',
        ' 4      return "Hi"',
    ):
        code.textLine(line)
    pdf.drawText(code)
    pdf.save()
    return path


def _sample_image() -> Image:
    from PIL import Image as PilImage
    from PIL import ImageDraw

    bitmap = PilImage.new("RGB", (240, 80), "#F5EEE3")
    draw = ImageDraw.Draw(bitmap)
    draw.rectangle((2, 2, 237, 77), outline="#E8552F", width=3)
    draw.text((18, 30), "embedded image", fill="#20231F")
    buffer = io.BytesIO()
    bitmap.save(buffer, format="PNG")
    buffer.seek(0)
    return Image(buffer, width=240, height=80)
