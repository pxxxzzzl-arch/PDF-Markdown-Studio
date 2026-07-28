from __future__ import annotations

from pdfmd.models import (
    BlockType,
    ConversionOptions,
    DocumentBlock,
    DocumentKind,
    DocumentPage,
    ParsedDocument,
)
from pdfmd.renderer import MarkdownRenderer


def test_renderer_supports_structural_blocks() -> None:
    document = ParsedDocument(
        source_filename="report.pdf",
        source_sha256="a" * 64,
        title="Quarterly Report",
        page_count=1,
        kind=DocumentKind.BORN_DIGITAL,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id="title",
                        type=BlockType.TITLE,
                        page=1,
                        text="Report",
                        engine="test",
                    ),
                    DocumentBlock(
                        id="p",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text="Body",
                        engine="test",
                    ),
                    DocumentBlock(
                        id="li",
                        type=BlockType.LIST_ITEM,
                        page=1,
                        text="Item",
                        engine="test",
                    ),
                    DocumentBlock(
                        id="table",
                        type=BlockType.TABLE,
                        page=1,
                        table_html="<table><tr><td>A</td></tr></table>",
                        engine="test",
                    ),
                    DocumentBlock(
                        id="image",
                        type=BlockType.IMAGE,
                        page=1,
                        text="Chart",
                        asset_path="assets/chart one.png",
                        engine="test",
                    ),
                ],
            )
        ],
    )
    markdown = MarkdownRenderer().render(document, ConversionOptions(preserve_page_markers=True))
    assert 'title: "Quarterly Report"' in markdown
    assert "<!-- page: 1 -->" in markdown
    assert "# Report" in markdown
    assert "- Item" in markdown
    assert "<table>" in markdown
    assert "![Chart](assets/chart%20one.png)" in markdown
    assert markdown.endswith("\n")


def test_raw_markdown_is_preserved() -> None:
    document = ParsedDocument(
        source_filename="raw.pdf",
        source_sha256="b" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="ocr",
                blocks=[
                    DocumentBlock(
                        id="raw",
                        type=BlockType.RAW_MARKDOWN,
                        page=1,
                        text="## OCR title\n\n| A | B |\n|---|---|\n| 1 | 2 |",
                        engine="ocr",
                    )
                ],
            )
        ],
    )
    markdown = MarkdownRenderer().render(
        document,
        ConversionOptions(include_front_matter=False, preserve_page_markers=False),
    )
    assert markdown.startswith("## OCR title")
    assert "| 1 | 2 |" in markdown


def test_renderer_escapes_metadata_and_uses_safe_code_fence() -> None:
    document = ParsedDocument(
        source_filename='folder\\name "quoted".pdf',
        source_sha256="d" * 64,
        title='A "quoted" title\nsecond line',
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id="code",
                        type=BlockType.CODE,
                        page=1,
                        text="before\n```\n````\nafter",
                        engine="test",
                        metadata={"language": "py<script>"},
                    ),
                    DocumentBlock(
                        id="header",
                        type=BlockType.PAGE_HEADER,
                        page=1,
                        text="repeated header",
                        engine="test",
                    ),
                ],
            )
        ],
    )
    markdown = MarkdownRenderer().render(document, ConversionOptions())
    assert 'title: "A \\"quoted\\" title second line"' in markdown
    assert "`````pyscript" in markdown
    assert "repeated header" not in markdown


def test_renderer_preserves_code_indentation_and_merges_cross_page_continuation() -> None:
    document = ParsedDocument(
        source_filename="code.pdf",
        source_sha256="e" * 64,
        page_count=2,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="code-1",
                        type=BlockType.CODE,
                        page=1,
                        text="def answer():\n    value = 42",
                        engine="docling",
                        metadata={},
                    )
                ],
            ),
            DocumentPage(
                number=2,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="code-2",
                        type=BlockType.CODE,
                        page=2,
                        text="    return value",
                        engine="docling",
                        metadata={
                            "language": "python",
                            "language_source": "docling",
                            "continues_previous": True,
                        },
                    )
                ],
            ),
        ],
    )

    markdown = MarkdownRenderer().render(
        document,
        ConversionOptions(include_front_matter=False, preserve_page_markers=False),
    )

    assert markdown.count("```python") == 1
    assert "def answer():\n    value = 42\n    return value" in markdown
    assert "<!-- page:" not in markdown


def test_renderer_page_markers_do_not_split_cross_page_code() -> None:
    document = ParsedDocument(
        source_filename="code-with-pages.pdf",
        source_sha256="f" * 64,
        page_count=2,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="code-1",
                        type=BlockType.CODE,
                        page=1,
                        text="def answer():\n    value = 42",
                        engine="docling",
                        metadata={"language": "python"},
                    )
                ],
            ),
            DocumentPage(
                number=2,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="code-2",
                        type=BlockType.CODE,
                        page=2,
                        text="    return value",
                        engine="docling",
                        metadata={"language": "python", "continues_previous": True},
                    ),
                    DocumentBlock(
                        id="paragraph-2",
                        type=BlockType.PARAGRAPH,
                        page=2,
                        text="Following paragraph.",
                        engine="docling",
                    ),
                ],
            ),
        ],
    )

    markdown = MarkdownRenderer().render(
        document,
        ConversionOptions(include_front_matter=False, preserve_page_markers=True),
    )

    assert markdown.count("```") == 2
    assert markdown.count("```python") == 1
    assert "def answer():\n    value = 42\n    return value" in markdown
    assert "<!-- code spans pages: 1, 2 -->" in markdown
    assert markdown.count("<!-- page: 1 -->") == 1
    assert markdown.count("<!-- page: 2 -->") == 1
    assert markdown.index("<!-- page: 2 -->") > markdown.rindex("```")


def test_renderer_merges_continuation_across_numeric_noise_and_reinfers_language() -> None:
    document = ParsedDocument(
        source_filename="noisy-continuation.pdf",
        source_sha256="a" * 64,
        page_count=2,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="code-start",
                        type=BlockType.CODE,
                        page=1,
                        text="# 初始化\nitems = [",
                        engine="docling",
                    )
                ],
            ),
            DocumentPage(
                number=2,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="line-number-noise",
                        type=BlockType.LIST_ITEM,
                        page=2,
                        text="1",
                        engine="docling",
                    ),
                    DocumentBlock(
                        id="code-end",
                        type=BlockType.CODE,
                        page=2,
                        text='    {"name": "Ada"}\n]\nprint(items)',
                        engine="docling",
                        metadata={
                            "continues_previous": True,
                            "language": "json",
                            "language_source": "layout_heuristic",
                        },
                    ),
                ],
            ),
        ],
    )

    markdown = MarkdownRenderer().render(
        document,
        ConversionOptions(include_front_matter=False, preserve_page_markers=True),
    )

    assert markdown.count("```python") == 1
    assert "```json" not in markdown
    assert "- 1" not in markdown
    assert '# 初始化\nitems = [\n    {"name": "Ada"}\n]\nprint(items)' in markdown
    assert "<!-- code spans pages: 1, 2 -->" in markdown
    assert markdown.index("<!-- page: 2 -->") > markdown.rindex("```")
