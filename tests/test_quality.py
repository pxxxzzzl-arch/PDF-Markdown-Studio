from __future__ import annotations

from pathlib import Path

from pdfmd.models import (
    BlockType,
    BoundingBox,
    DocumentBlock,
    DocumentKind,
    DocumentPage,
    IssueSeverity,
    PageInspection,
    ParsedDocument,
    PdfInspection,
)
from pdfmd.quality import evaluate_quality


def _inspection(native_chars: int = 100) -> PdfInspection:
    return PdfInspection(
        path="test.pdf",
        filename="test.pdf",
        sha256="c" * 64,
        file_size=100,
        page_count=1,
        kind=DocumentKind.BORN_DIGITAL,
        pages=[PageInspection(number=1, native_text_chars=native_chars)],
    )


def test_quality_flags_empty_nonblank_page(tmp_path: Path) -> None:
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="test")],
    )
    report = evaluate_quality(
        document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert not report.passed
    assert report.fallback_pages == [1]
    assert any(issue.code == "empty_output" for issue in report.issues)


def test_quality_accepts_complete_text(tmp_path: Path) -> None:
    text = "x" * 110
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id="p1",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text=text,
                        engine="test",
                    )
                ],
            )
        ],
    )
    report = evaluate_quality(
        document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert report.passed
    assert report.score == 100
    assert report.fallback_pages == []


def test_quality_detects_missing_asset(tmp_path: Path) -> None:
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id="p1", type=BlockType.PARAGRAPH, page=1, text="x" * 100, engine="test"
                    ),
                    DocumentBlock(
                        id="img",
                        type=BlockType.IMAGE,
                        page=1,
                        asset_path="assets/missing.png",
                        engine="test",
                    ),
                ],
            )
        ],
    )
    report = evaluate_quality(
        document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert any(issue.code == "missing_asset" for issue in report.issues)


def test_quality_counts_visible_html_table_text(tmp_path: Path) -> None:
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="table",
                        type=BlockType.TABLE,
                        page=1,
                        table_html="<table><tr><td>" + ("cell " * 25) + "</td></tr></table>",
                        engine="docling",
                    )
                ],
            )
        ],
    )
    report = evaluate_quality(
        document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )
    assert report.passed
    assert report.pages[0].extracted_chars >= 100


def test_quality_detects_duplicate_and_garbled_content(tmp_path: Path) -> None:
    duplicate_document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id=f"p1-{index}",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text="repeated content block " * 2,
                        engine="test",
                    )
                    for index in range(5)
                ],
            )
        ],
    )
    duplicate_report = evaluate_quality(
        duplicate_document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert any(issue.code == "duplicate_content" for issue in duplicate_report.issues)
    assert duplicate_report.pages[0].duplicate_ratio > 0.5

    garbled_document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id="garbled",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text="valid" * 20 + "\ufffd" * 10,
                        engine="test",
                    )
                ],
            )
        ],
    )
    garbled_report = evaluate_quality(
        garbled_document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert not garbled_report.passed
    assert any(issue.code == "garbled_text" for issue in garbled_report.issues)


def test_quality_detects_missing_page_and_empty_table(tmp_path: Path) -> None:
    missing = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[],
    )
    missing_report = evaluate_quality(
        missing,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert missing_report.score == 0
    assert any(issue.code == "missing_page" for issue in missing_report.issues)

    empty_table = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="test",
                blocks=[
                    DocumentBlock(
                        id="body",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text="content " * 20,
                        engine="test",
                    ),
                    DocumentBlock(
                        id="table",
                        type=BlockType.TABLE,
                        page=1,
                        engine="test",
                    ),
                ],
            )
        ],
    )
    table_report = evaluate_quality(
        empty_table,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="test",
    )
    assert any(issue.code == "empty_table" for issue in table_report.issues)


def test_quality_aggregates_code_layout_findings_per_page(tmp_path: Path) -> None:
    collapsed = (
        "from langchain_core.prompts import ChatPromptTemplate "
        "template = ChatPromptTemplate.from_messages([('system', role), ('user', question)]) "
        "result = template.invoke({'role': role, 'question': question}) "
        "print(result) 1 2 3 4 5 6 7 8"
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="code-1",
                        type=BlockType.CODE,
                        page=1,
                        text=collapsed,
                        engine="docling",
                    ),
                    DocumentBlock(
                        id="code-2",
                        type=BlockType.CODE,
                        page=1,
                        text=collapsed,
                        engine="docling",
                    ),
                    DocumentBlock(
                        id="orphan-1",
                        type=BlockType.CODE,
                        page=1,
                        text='"}',
                        engine="docling",
                    ),
                    DocumentBlock(
                        id="orphan-2",
                        type=BlockType.CODE,
                        page=1,
                        text=")",
                        engine="docling",
                    ),
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=500),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not report.passed
    assert report.fallback_pages == [1]
    for code in (
        "collapsed_code_layout",
        "line_number_contamination",
        "orphan_code_fragment",
    ):
        matching = [issue for issue in report.issues if issue.code == code]
        assert len(matching) == 1
        assert matching[0].page == 1


def test_quality_accepts_one_logical_line_rejoined_from_visual_wraps(tmp_path: Path) -> None:
    long_repr = (
        "ChatPromptValue(messages=[SystemMessage(content='assistant', additional_kwargs={}), "
        "HumanMessage(content='hello', additional_kwargs={}), AIMessage(content='world', "
        "response_metadata={}, tool_calls=[], invalid_tool_calls=[])])"
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="wrapped-output",
                        type=BlockType.CODE,
                        page=1,
                        text=long_repr,
                        engine="docling",
                        metadata={
                            "layout_recovered": True,
                            "start_line": 1,
                            "end_line": 1,
                        },
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=len(long_repr)),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not any(issue.code == "collapsed_code_layout" for issue in report.issues)
    assert report.passed


def test_quality_rejects_short_collapsed_python_statements(tmp_path: Path) -> None:
    collapsed = (
        "def fibonacci(n: int) -> int: if n < 2: return n "
        "print(fibonacci(10))  # expected: 55"
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="collapsed-python",
                        type=BlockType.CODE,
                        page=1,
                        text=collapsed,
                        engine="docling",
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=len(collapsed)),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    issue = next(issue for issue in report.issues if issue.code == "collapsed_code_layout")
    assert issue.severity is IssueSeverity.ERROR
    assert report.fallback_pages == [1]
    assert not report.passed


def test_quality_accepts_valid_single_line_python_statements(tmp_path: Path) -> None:
    source = "first = 1; second = 2; result = first + second; print(result)"
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="valid-single-line",
                        type=BlockType.CODE,
                        page=1,
                        text=source,
                        metadata={"language": "python"},
                        engine="docling",
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=len(source)),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not any(issue.code == "collapsed_code_layout" for issue in report.issues)
    assert report.passed


def test_quality_detects_excess_coverage_and_flat_heading_hierarchy(tmp_path: Path) -> None:
    blocks = [
        DocumentBlock(
            id=f"heading-{index}",
            type=BlockType.HEADING,
            page=1,
            text=f"1.{index} 小节",
            level=2,
            engine="docling",
        )
        for index in range(1, 9)
    ]
    blocks.append(
        DocumentBlock(
            id="body",
            type=BlockType.PARAGRAPH,
            page=1,
            text="正文内容" * 70,
            engine="docling",
        )
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="docling", blocks=blocks)],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=100),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not report.passed
    assert report.fallback_pages == [1]
    assert sum(issue.code == "flat_heading_hierarchy" for issue in report.issues) == 1
    assert sum(issue.code == "excess_text_coverage" for issue in report.issues) == 1


def test_quality_treats_structured_table_expansion_as_warning(tmp_path: Path) -> None:
    table = DocumentBlock(
        id="table",
        type=BlockType.TABLE,
        page=1,
        text="| 字段 | 说明 |\n| --- | --- |\n| value | " + "完整单元格内容" * 40 + " |",
        metadata={"table_rows": 2, "table_columns": 2},
        engine="docling",
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="docling", blocks=[table])],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=100),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    issue = next(issue for issue in report.issues if issue.code == "excess_text_coverage")
    assert issue.severity is IssueSeverity.WARNING
    assert report.pages[0].score == 85
    assert report.fallback_pages == []
    assert report.passed


def test_quality_aggregates_tiny_assets_and_table_mismatches(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "tiny-1.png").write_bytes(b"not-an-image")
    (assets / "tiny-2.png").write_bytes(b"not-an-image")
    image_blocks = [
        DocumentBlock(
            id=f"tiny-{index}",
            type=BlockType.IMAGE,
            page=1,
            asset_path=f"assets/tiny-{index}.png",
            bbox=BoundingBox(left=0, top=12, right=12, bottom=0),
            engine="docling",
        )
        for index in (1, 2)
    ]
    table = DocumentBlock(
        id="table",
        type=BlockType.TABLE,
        page=1,
        table_html=(
            "<table><tr><th>A</th><th>B</th></tr><tr><td>one</td><td>two</td></tr></table>"
        ),
        metadata={"table_structure": {"num_rows": 3, "num_cols": 2}},
        engine="docling",
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="body",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text="content " * 20,
                        engine="docling",
                    ),
                    *image_blocks,
                    table,
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=200),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not report.passed
    assert report.fallback_pages == [1]
    assert sum(issue.code == "tiny_asset" for issue in report.issues) == 1
    assert sum(issue.code == "table_structure_mismatch" for issue in report.issues) == 1


def test_quality_detects_broken_gfm_table_columns(tmp_path: Path) -> None:
    table = DocumentBlock(
        id="broken-gfm",
        type=BlockType.TABLE,
        page=1,
        text="| A | B |\n| --- | --- |\n| value | left | right |",
        metadata={"table_rows": 2, "table_columns": 2},
        engine="docling",
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="docling", blocks=[table])],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=40),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not report.passed
    assert report.fallback_pages == [1]
    assert any(issue.code == "table_structure_mismatch" for issue in report.issues)


def test_quality_marks_aligned_image_code_for_human_review(tmp_path: Path) -> None:
    code = DocumentBlock(
        id="image-code",
        type=BlockType.CODE,
        page=1,
        text="\n".join(f"value_{index} = {index}" for index in range(20)),
        metadata={
            "image_layout_recovered": True,
            "image_layout_confidence": 0.98,
            "image_layout_similarity": 0.97,
        },
        engine="docling",
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="docling", blocks=[code])],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=100),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    issue = next(issue for issue in report.issues if issue.code == "recovered_image_text_expansion")
    assert issue.severity is IssueSeverity.INFO
    review = next(issue for issue in report.issues if issue.code == "image_ocr_review_required")
    assert review.severity is IssueSeverity.WARNING
    assert report.passed
    assert report.metrics["image_ocr_code_blocks"] == 1
    assert report.metrics["code_block_count"] == 1


def test_quality_warns_when_image_ocr_python_is_not_parseable(tmp_path: Path) -> None:
    code = DocumentBlock(
        id="broken-image-code",
        type=BlockType.CODE,
        page=1,
        text="from dotenv import load_dotenv\nLoad_dotenv(\nprint('missing close')",
        metadata={
            "image_layout_recovered": True,
            "image_layout_confidence": "malformed",
            "image_layout_similarity": None,
            "language": "python",
        },
        engine="docling",
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="docling", blocks=[code])],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=60),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert any(issue.code == "image_ocr_review_required" for issue in report.issues)
    assert any(issue.code == "image_ocr_syntax_warning" for issue in report.issues)


def test_quality_validates_python_after_merging_cross_page_continuations(
    tmp_path: Path,
) -> None:
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=2,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="part-1",
                        type=BlockType.CODE,
                        page=1,
                        text="def answer():\n    value = (",
                        metadata={"language": "python", "layout_recovered": True},
                        engine="docling",
                    )
                ],
            ),
            DocumentPage(
                number=2,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="part-2",
                        type=BlockType.CODE,
                        page=2,
                        text="        40 + 2\n    )\n    return value",
                        metadata={
                            "language": "python",
                            "layout_recovered": True,
                            "continues_previous": True,
                        },
                        engine="docling",
                    )
                ],
            ),
        ],
    )
    inspection = PdfInspection(
        path="test.pdf",
        filename="test.pdf",
        sha256="c" * 64,
        file_size=100,
        page_count=2,
        kind=DocumentKind.BORN_DIGITAL,
        pages=[
            PageInspection(number=1, native_text_chars=25),
            PageInspection(number=2, native_text_chars=30),
        ],
    )

    report = evaluate_quality(
        document,
        inspection,
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not any(issue.code == "python_syntax_error" for issue in report.issues)
    assert report.metrics["logical_code_block_count"] == 1
    assert report.metrics["checked_code_block_count"] == 1
    assert report.metrics["invalid_code_block_count"] == 0


def test_quality_rejects_long_corrupted_python_source(tmp_path: Path) -> None:
    broken = "\n".join(
        [
            "import json",
            "class Handler:",
            "    def build(self):",
            "        response = {",
            '            "name": payload["tools"][0]nction"]["name"],',
            '            "value": 1,',
            "        }",
            "        return response",
        ]
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="broken-python",
                        type=BlockType.CODE,
                        page=1,
                        text=broken,
                        metadata={"language": "python", "layout_recovered": True},
                        engine="docling",
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=len(broken)),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    issue = next(issue for issue in report.issues if issue.code == "python_syntax_error")
    assert issue.severity is IssueSeverity.ERROR
    assert report.fallback_pages == [1]
    assert not report.passed
    assert report.metrics["invalid_code_block_count"] == 1


def test_quality_checks_python_fences_inside_raw_markdown(tmp_path: Path) -> None:
    document = ParsedDocument(
        source_filename="scan.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="paddleocr",
                blocks=[
                    DocumentBlock(
                        id="raw",
                        type=BlockType.RAW_MARKDOWN,
                        page=1,
                        text=(
                            "说明\n\n```python\n"
                            "def broken(\n"
                            "    value = 1\n"
                            "    return value\n"
                            "    print(value)\n"
                            "    value += 1\n"
                            "    return value\n"
                            "    pass\n"
                            "```\n"
                        ),
                        engine="paddleocr",
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=80),
        tmp_path,
        minimum_score=72,
        primary_engine="paddleocr",
    )

    assert any(issue.code == "python_syntax_error" for issue in report.issues)
    assert report.metrics["checked_code_block_count"] == 1
    assert report.metrics["invalid_code_block_count"] == 1
    assert not report.passed


def test_quality_detects_unclosed_raw_markdown_fence(tmp_path: Path) -> None:
    document = ParsedDocument(
        source_filename="scan.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="paddleocr",
                blocks=[
                    DocumentBlock(
                        id="raw",
                        type=BlockType.RAW_MARKDOWN,
                        page=1,
                        text="```python\ndef unfinished():\n    return 1",
                        engine="paddleocr",
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=40),
        tmp_path,
        minimum_score=72,
        primary_engine="paddleocr",
    )

    assert any(issue.code == "unclosed_code_fence" for issue in report.issues)
    assert report.metrics["unclosed_code_fence_count"] == 1
    assert report.metrics["checked_code_block_count"] == 1
    assert report.metrics["code_quality_score"] == 0
    assert not report.passed


def test_quality_exposes_compact_docling_confidence_metrics(tmp_path: Path) -> None:
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="text",
                        type=BlockType.PARAGRAPH,
                        page=1,
                        text="x" * 100,
                        engine="docling",
                    )
                ],
            )
        ],
        metadata={
            "docling_confidence": {
                "mean_grade": "excellent",
                "pages": {
                    "1": {"parse_score": 0.98, "layout_score": 0.91},
                    "2": {"parse_score": 0.88, "layout_score": None},
                },
            }
        },
    )

    report = evaluate_quality(
        document,
        _inspection(),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert report.metrics["docling_mean_grade"] == "excellent"
    assert report.metrics["docling_mean_parse_score"] == 0.93
    assert report.metrics["docling_low_parse_score"] == 0.88
    assert report.metrics["docling_mean_layout_score"] == 0.91


def test_quality_reclassifies_partial_json_continuation_as_complete_python(
    tmp_path: Path,
) -> None:
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="start",
                        type=BlockType.CODE,
                        page=1,
                        text="# 初始化\nitems = [",
                        engine="docling",
                    ),
                    DocumentBlock(
                        id="end",
                        type=BlockType.CODE,
                        page=1,
                        text='    {"name": "Ada"}\n]\nprint(items)',
                        engine="docling",
                        metadata={
                            "continues_previous": True,
                            "language": "json",
                            "language_source": "layout_heuristic",
                        },
                    ),
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=60),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not any(issue.code == "json_syntax_error" for issue in report.issues)
    assert report.metrics["checked_code_block_count"] == 1
    assert report.metrics["invalid_code_block_count"] == 0
    assert report.metrics["reclassified_language_code_block_count"] == 1


def test_quality_does_not_parse_sdk_message_representation_as_source(tmp_path: Path) -> None:
    output = (
        "AIMessage(\n"
        "    content='hello,\n"
        "    additional_kwargs={\n"
        "        'refusal': None,\n"
        "    },\n"
        "    response_metadata={\n"
        "        'tokens': 12,\n"
        "    }\n"
    )
    document = ParsedDocument(
        source_filename="test.pdf",
        source_sha256="c" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                engine="docling",
                blocks=[
                    DocumentBlock(
                        id="message-output",
                        type=BlockType.CODE,
                        page=1,
                        text=output,
                        engine="docling",
                        metadata={
                            "language": "python",
                            "image_layout_recovered": True,
                            "image_layout_confidence": 0.98,
                            "image_layout_similarity": 0.98,
                        },
                    )
                ],
            )
        ],
    )

    report = evaluate_quality(
        document,
        _inspection(native_chars=len(output)),
        tmp_path,
        minimum_score=72,
        primary_engine="docling",
    )

    assert not any("syntax" in issue.code for issue in report.issues)
    assert report.metrics["checked_code_block_count"] == 0
    assert report.metrics["invalid_code_block_count"] == 0
