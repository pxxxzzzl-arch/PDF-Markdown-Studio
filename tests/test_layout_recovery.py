from __future__ import annotations

from pathlib import Path

from pdfmd.layout_recovery import recover_code_layout
from pdfmd.models import BlockType, BoundingBox, DocumentBlock


class _FakePage:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self, *, extraction_mode: str) -> str:
        assert extraction_mode == "layout"
        return self.text


class _FakeReader:
    layouts: list[str] = []

    def __init__(self, _path: str, *, strict: bool):
        assert strict is False
        self.pages = [_FakePage(text) for text in self.layouts]


def _block(
    block_id: str,
    text: str,
    *,
    page: int = 1,
    block_type: BlockType = BlockType.CODE,
    top: float = 700,
    bottom: float = 500,
) -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        type=block_type,
        page=page,
        text=text,
        bbox=BoundingBox(left=80, top=top, right=500, bottom=bottom),
        engine="docling",
    )


def test_recovers_newlines_blank_lines_indent_and_protects_numeric_literals(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
   1   values = [1, 2, 3]
   2
   3   def total():
   4       return 42
   5   print(total())
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [
            _block(
                "code",
                "values = [1, 2, 3] def total(): return 42 print(total()) 1 2 3 4 5",
            )
        ]
    }

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].text == (
        "values = [1, 2, 3]\n\ndef total():\n    return 42\nprint(total())"
    )
    assert blocks[1][0].metadata["layout_recovered"] is True
    assert blocks[1][0].metadata["start_line"] == 1
    assert blocks[1][0].metadata["end_line"] == 5
    assert blocks[1][0].metadata["language"] == "python"
    assert stats.blocks_recovered == 1
    assert stats.fragments_merged == 0


def test_does_not_treat_plain_numbered_prose_as_code(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1 First preparation step
  2 Second preparation step
  3 Third preparation step
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    original = "First preparation step Second preparation step Third preparation step"
    blocks = {1: [_block("paragraph", original, block_type=BlockType.PARAGRAPH)]}

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].type is BlockType.PARAGRAPH
    assert blocks[1][0].text == original
    # It may be retained as a low-confidence layout candidate, but must never
    # promote or replace a prose block.
    assert stats.candidates_found == 1
    assert stats.blocks_recovered == 0


def test_merges_overlapping_docling_fragments(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1   from templates import PromptLibrary
  2   messages = PromptLibrary.TRANSLATOR.format_messages(
  3       source_lang="English",
  4       target_lang="Chinese",
  5   )
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [
            _block("part-1", "from templates import PromptLibrary", top=800, bottom=650),
            _block(
                "part-2",
                'messages = PromptLibrary.TRANSLATOR.format_messages( source_lang="English" '
                'target_lang="Chinese" ) 1 2 3 4 5',
                top=760,
                bottom=600,
            ),
        ]
    }

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert len(blocks[1]) == 1
    assert blocks[1][0].text.splitlines() == [
        "from templates import PromptLibrary",
        "messages = PromptLibrary.TRANSLATOR.format_messages(",
        '    source_lang="English",',
        '    target_lang="Chinese",',
        ")",
    ]
    assert stats.blocks_recovered == 1
    assert stats.fragments_merged == 1


def test_promotes_only_a_strongly_matching_paragraph(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1   result = client.invoke(
  2       {"question": "hello"}
  3   )
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [
            _block(
                "paragraph",
                'result = client.invoke( {"question": "hello"} ) 1 2 3',
                block_type=BlockType.PARAGRAPH,
            )
        ]
    }

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].type is BlockType.CODE
    assert blocks[1][0].text == 'result = client.invoke(\n    {"question": "hello"}\n)'
    assert stats.paragraphs_promoted == 1


def test_recovers_unnumbered_code_and_separates_adjacent_aligned_table(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
    def fibonacci(n: int) -> int:
        if n < 2:
            return n
        return fibonacci(n - 1) + fibonacci(n - 2)


    print(fibonacci(10))  # expected: 55



Metric     Target     Result
Code       fenced     Pass
Order      stable     Pass
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [
            _block(
                "code-1",
                "def fibonacci(n: int) -> int: if n < 2: return n "
                "print(fibonacci(10))  # expected: 55",
                top=726,
                bottom=644,
            ),
            _block(
                "code-2",
                "return fibonacci(n - 1) + fibonacci(n - 2) "
                "Metric     Target     Result Code       fenced     Pass "
                "Order      stable     Pass",
                top=681,
                bottom=562,
            ),
        ]
    }

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert len(blocks[1]) == 2
    assert blocks[1][0].type is BlockType.CODE
    assert blocks[1][0].text == (
        "def fibonacci(n: int) -> int:\n"
        "    if n < 2:\n"
        "        return n\n"
        "    return fibonacci(n - 1) + fibonacci(n - 2)\n\n"
        "print(fibonacci(10))  # expected: 55"
    )
    assert blocks[1][0].metadata["layout_source"] == "unnumbered_text_layer"
    assert blocks[1][0].metadata["language"] == "python"
    assert blocks[1][1].type is BlockType.TABLE
    assert blocks[1][1].text.splitlines() == [
        "| Metric | Target | Result |",
        "| --- | --- | --- |",
        "| Code | fenced | Pass |",
        "| Order | stable | Pass |",
    ]
    assert blocks[1][1].metadata["layout_table_recovered"] is True
    assert stats.blocks_recovered == 1


def test_does_not_convert_aligned_prose_or_control_flow_into_table(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
First sentence     second phrase
Another sentence   another phrase
Final sentence     final phrase

if x:      return 1
if y:      return 2
if z:      return 3
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    prose = _block(
        "prose",
        "First sentence second phrase Another sentence another phrase "
        "Final sentence final phrase",
        block_type=BlockType.PARAGRAPH,
    )
    code = _block(
        "code",
        "if x: return 1 if y: return 2 if z: return 3",
        top=480,
        bottom=400,
    )
    blocks = {1: [prose, code]}

    recover_code_layout(Path("sample.pdf"), blocks)

    assert prose.type is BlockType.PARAGRAPH
    assert code.type is BlockType.CODE
    assert code.text.splitlines() == [
        "if x:      return 1",
        "if y:      return 2",
        "if z:      return 3",
    ]


def test_does_not_remove_code_phrase_from_adjacent_explanation(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
def build():
    return fibonacci(n - 1) + fibonacci(n - 2)
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    explanation = (
        "The expression return fibonacci(n - 1) + fibonacci(n - 2) "
        "is the recursive step discussed below."
    )
    blocks = {
        1: [
            _block("code", "def build(): return fibonacci(n - 1) + fibonacci(n - 2)"),
            _block("explanation", explanation, block_type=BlockType.PARAGRAPH),
        ]
    }

    recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][1].type is BlockType.PARAGRAPH
    assert blocks[1][1].text == explanation


def test_code_word_table_is_not_folded_into_unnumbered_code(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
Class       Function       Return
Client      send           value
Worker      process        result
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    original = "Class Function Return Client send value Worker process result"
    block = _block("prose", original, block_type=BlockType.PARAGRAPH)
    blocks = {1: [block]}

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert block.type is BlockType.PARAGRAPH
    assert block.text == original
    assert stats.blocks_recovered == 0
    assert stats.layout_tables_recovered == 0


def test_marks_consecutive_cross_page_code(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1   def build():
  2       value = 40
  3       return value + 2
""",
        """
  4   result = build()
  5   assert result == 42
  6   print(result)
""",
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [_block("page-1", "def build(): value = 40 return value + 2 1 2 3")],
        2: [
            _block(
                "page-2",
                "result = build() assert result == 42 print(result) 4 5 6",
                page=2,
            )
        ],
    }

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert "continues_previous" not in blocks[1][0].metadata
    assert blocks[2][0].metadata["continues_previous"] is True
    assert stats.cross_page_continuations == 1


def test_joins_unnumbered_visual_wrap_into_previous_logical_code_line(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  18   return f"data:image/{img_type};base64,
       {base64.b64encode(img_file.read()).decode('utf-8')}"
  19   print(result)
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [
            _block(
                "code",
                'return f"data:image/{img_type};base64,'
                "{base64.b64encode(img_file.read()).decode('utf-8')}\" print(result) 18 19",
            )
        ]
    }

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].text == (
        'return f"data:image/{img_type};base64,'
        "{base64.b64encode(img_file.read()).decode('utf-8')}\"\n"
        "print(result)"
    )
    assert stats.blocks_recovered == 1


def test_visual_wrap_left_of_numbered_payload_never_loses_characters(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
20               print(repr(error))
21
22           response = {
23                                       "name": payload["tools"][0]
     ["function"]["name"],
24                                       "arguments": {
     'director': 'Ada',
25                                       }
26           }
27       def log_message(self):
28           pass
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    flattened = (
        'print(repr(error)) response = { "name": payload["tools"][0] '
        '["function"]["name"], "arguments": { \'director\': \'Ada\', } } '
        "def log_message(self): pass 20 21 22 23 24 25 26 27 28"
    )
    blocks = {1: [_block("continued-code", flattened)]}

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert stats.blocks_recovered == 1
    recovered = blocks[1][0].text
    assert 'payload["tools"][0]["function"]["name"]' in recovered
    assert "'director': 'Ada'," in recovered
    assert blocks[1][0].metadata["start_line"] == 20
    assert blocks[1][0].metadata["end_line"] == 28


def test_language_inference_does_not_label_markdown_heading_as_python(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1   ### 直观理解
  2   这是普通的 Markdown 解释。
  3   - 第一项
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {1: [_block("markdown-output", "### 直观理解 这是普通的 Markdown 解释。 - 第一项")]}

    recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].metadata.get("language", "") == ""


def test_python_comment_heading_does_not_hide_real_source_code(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1   # 1. 调用模型
  2   prompt = "hello"
  3   result = model.invoke(prompt)
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    blocks = {
        1: [
            _block(
                "python-with-heading",
                '# 1. 调用模型 prompt = "hello" result = model.invoke(prompt)',
            )
        ]
    }

    recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].metadata["language"] == "python"


def test_empty_language_inference_preserves_upstream_language(monkeypatch) -> None:
    _FakeReader.layouts = [
        """
  1   plain output
  2   another output
  3   final output
"""
    ]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    block = _block("output", "plain output another output final output")
    block.metadata["language"] = "text"
    blocks = {1: [block]}

    recover_code_layout(Path("sample.pdf"), blocks)

    assert block.metadata["language"] == "text"


def test_does_not_delete_legitimate_consecutive_numeric_arguments(monkeypatch) -> None:
    _FakeReader.layouts = ["This page has no numbered code gutter."]
    monkeypatch.setattr("pdfmd.layout_recovery.PdfReader", _FakeReader)
    original = "run_benchmark --ports 8000 8001 8002 8003"
    blocks = {1: [_block("numeric-arguments", original)]}

    stats = recover_code_layout(Path("sample.pdf"), blocks)

    assert blocks[1][0].text == original
    assert "line_number_tail_removed" not in blocks[1][0].metadata
    assert stats.line_number_tails_removed == 0
