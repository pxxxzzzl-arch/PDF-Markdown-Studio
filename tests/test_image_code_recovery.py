from __future__ import annotations

from pathlib import Path

from pdfmd.image_code_recovery import (
    OcrRecord,
    _PlacedImage,
    _repair_ocr_code,
    reconstruct_ocr_candidates,
    recover_embedded_image_code,
)
from pdfmd.models import BlockType, BoundingBox, DocumentBlock


def _record(x: float, y: float, text: str, width: float = 160) -> OcrRecord:
    return OcrRecord(
        box=((x, y), (x + width, y), (x + width, y + 20), (x, y + 20)),
        text=text,
        confidence=0.98,
    )


def test_reconstructs_numbered_code_with_blank_line_and_indentation() -> None:
    records = [
        _record(5, 0, "5", 15),
        _record(42, 0, "# load environment", 180),
        _record(5, 30, "6", 15),
        _record(42, 30, "load_dotenv(override=True)", 260),
        _record(5, 60, "7", 15),
        _record(5, 90, "8", 15),
        _record(42, 90, "model = init_chat_model(", 240),
        _record(5, 120, "9", 15),
        _record(90, 120, 'model="test",', 140),
        _record(5, 150, "10", 20),
        _record(42, 150, ")", 12),
        _record(42, 180, "[1] 4s 592ms", 150),
    ]

    recovered = reconstruct_ocr_candidates(records)

    assert len(recovered) == 1
    assert recovered[0].line_numbers_removed is True
    assert recovered[0].text == (
        "# load environment\n"
        "load_dotenv(override=True)\n\n"
        "model = init_chat_model(\n"
        '    model="test",\n'
        ")"
    )


def test_reconstructs_unumbered_output_using_box_indentation() -> None:
    records = [
        _record(40, 0, "AIMessage(", 120),
        _record(92, 30, "content='hello',", 180),
        _record(92, 60, "additional_kwargs={", 210),
        _record(148, 90, "'refusal': None,", 180),
        _record(92, 120, "},", 25),
        _record(40, 150, ")", 12),
    ]

    recovered = reconstruct_ocr_candidates(records)

    assert recovered[0].line_numbers_removed is False
    assert recovered[0].text.splitlines() == [
        "AIMessage(",
        "    content='hello',",
        "    additional_kwargs={",
        "        'refusal': None,",
        "    },",
        ")",
    ]


def test_reconstruction_never_changes_valid_c_identifier_to_parenthesis() -> None:
    records = [
        _record(40, 0, "call(", 70),
        _record(92, 30, "a,", 30),
        _record(92, 60, "c", 15),
        _record(40, 90, ")", 12),
    ]

    recovered = reconstruct_ocr_candidates(records)

    assert recovered[0].text.splitlines() == ["call(", "    a,", "    c", ")"]


def test_repairs_uppercase_closing_glyph_only_with_strong_syntax_context() -> None:
    repaired, repairs = _repair_ocr_code(
        [
            "Load_dotenv(override=True)",
            "model = init_chat_model(",
            '    model="deepseek",',
            '    extra_body={"thinking": {"type": "enabled"}},',
            "C",
            "# send one request",
            'response = model.invoke("hello")',
        ]
    )

    assert repaired == [
        "load_dotenv(override=True)",
        "model = init_chat_model(",
        '    model="deepseek",',
        '    extra_body={"thinking": {"type": "enabled"}},',
        ")",
        "# send one request",
        'response = model.invoke("hello")',
    ]
    assert repairs == (
        "normalize_load_dotenv_case",
        "standalone_C_to_closing_parenthesis",
    )


def test_integration_replaces_only_a_high_similarity_collapsed_code(monkeypatch) -> None:
    image = _PlacedImage(
        name="code.png",
        data=b"image",
        width=1000,
        height=300,
        ctm=(500, 0, 0, 150, 0, 0),
    )
    records = [
        _record(40, 10, "def build():", 130),
        _record(90, 50, "value = 42", 110),
        _record(90, 90, "return value", 120),
        _record(40, 130, "result = build()", 150),
        _record(40, 170, "print(result)", 120),
    ]
    monkeypatch.setattr("pdfmd.image_code_recovery._optional_runtime_available", lambda: True)
    monkeypatch.setattr("pdfmd.image_code_recovery._load_rapidocr_engine", lambda: object())
    monkeypatch.setattr("pdfmd.image_code_recovery._extract_placed_images", lambda _page: [image])
    monkeypatch.setattr("pdfmd.image_code_recovery._run_ocr", lambda _engine, _data: records)

    class _Reader:
        pages = [object()]

        def __init__(self, _path: str, *, strict: bool):
            assert strict is False

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    block = DocumentBlock(
        id="code",
        type=BlockType.CODE,
        page=1,
        text=(
            "def build(): value = 42 return value result = build() print(result) "
            "# construct and print the result"
        ),
        bbox=BoundingBox(left=0, top=150, right=500, bottom=0),
        engine="docling",
    )

    stats = recover_embedded_image_code(Path("sample.pdf"), {1: [block]})

    assert stats.blocks_recovered == 1
    assert block.text == (
        "def build():\n    value = 42\n    return value\nresult = build()\nprint(result)"
    )
    assert block.metadata["image_layout_recovered"] is True
    assert block.metadata["image_layout_similarity"] >= 0.68


def test_unrelated_ocr_never_replaces_code(monkeypatch) -> None:
    image = _PlacedImage(
        name="photo.png",
        data=b"image",
        width=1000,
        height=300,
        ctm=(500, 0, 0, 150, 0, 0),
    )
    records = [
        _record(40, 10, "summer holiday", 130),
        _record(40, 50, "beautiful beach", 130),
        _record(40, 90, "family memories", 130),
    ]
    monkeypatch.setattr("pdfmd.image_code_recovery._optional_runtime_available", lambda: True)
    monkeypatch.setattr("pdfmd.image_code_recovery._load_rapidocr_engine", lambda: object())
    monkeypatch.setattr("pdfmd.image_code_recovery._extract_placed_images", lambda _page: [image])
    monkeypatch.setattr("pdfmd.image_code_recovery._run_ocr", lambda _engine, _data: records)

    class _Reader:
        pages = [object()]

        def __init__(self, _path: str, *, strict: bool):
            pass

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    original = "def build(): value = 42 return value " * 3
    block = DocumentBlock(
        id="code",
        type=BlockType.CODE,
        page=1,
        text=original,
        bbox=BoundingBox(left=0, top=150, right=500, bottom=0),
        engine="docling",
    )

    stats = recover_embedded_image_code(Path("sample.pdf"), {1: [block]})

    assert stats.blocks_recovered == 0
    assert block.text == original
    assert "image_layout_recovered" not in block.metadata
