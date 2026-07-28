from __future__ import annotations

from pdfmd.models import (
    BlockType,
    BoundingBox,
    DocumentBlock,
    DocumentPage,
    ParsedDocument,
)
from pdfmd.postprocess import normalize_document, normalize_prose_spacing


def _block(
    block_id: str,
    block_type: BlockType,
    text: str,
    *,
    left: float = 10,
    right: float = 100,
    top: float = 100,
    bottom: float = 90,
) -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        type=block_type,
        page=1,
        text=text,
        bbox=BoundingBox(left=left, right=right, top=top, bottom=bottom),
        engine="test",
    )


def _document(blocks: list[DocumentBlock]) -> ParsedDocument:
    return ParsedDocument(
        source_filename="test.pdf",
        source_sha256="a" * 64,
        page_count=1,
        pages=[DocumentPage(number=1, engine="test", blocks=blocks)],
    )


def test_normalize_prose_repairs_cjk_wrap_spaces_only() -> None:
    assert normalize_prose_spacing("软件维 护规 则，AI 助手") == "软件维护规则，AI 助手"


def test_normalize_document_uses_numbered_heading_hierarchy_and_promotes_caption() -> None:
    result = normalize_document(
        _document(
            [
                _block("chapter", BlockType.HEADING, "第 04 章：消息"),
                _block("major", BlockType.HEADING, "1、认识消息", top=80, bottom=70),
                _block("section", BlockType.HEADING, "1.5 对象", top=60, bottom=50),
                _block("subsection", BlockType.HEADING, "1.5.1 参数", top=40, bottom=30),
                _block("method", BlockType.CAPTION, "方法 2：使用 + 运算符", top=20, bottom=10),
            ]
        )
    )
    blocks = result.pages[0].blocks
    assert [block.level for block in blocks] == [1, 2, 3, 4, 5]
    assert blocks[-1].type is BlockType.HEADING
    assert blocks[-1].metadata["promoted_from_caption"] is True


def test_normalize_document_clamps_unnumbered_heading_jumps() -> None:
    deep = _block("deep", BlockType.HEADING, "相当于", top=20, bottom=10)
    deep.level = 6
    result = normalize_document(
        _document(
            [
                _block("section", BlockType.HEADING, "1.5.1 参数", top=40, bottom=30),
                deep,
            ]
        )
    )

    assert [block.level for block in result.pages[0].blocks] == [4, 5]


def test_deep_semantic_enumerated_heading_is_not_flattened_to_h2() -> None:
    nested = _block("nested", BlockType.HEADING, "1、系统消息", top=20, bottom=10)
    nested.level = 6
    nested.metadata["semantic_level"] = 6
    result = normalize_document(
        _document(
            [
                _block("section", BlockType.HEADING, "1.2 消息类型", top=40, bottom=30),
                nested,
            ]
        )
    )

    assert [block.level for block in result.pages[0].blocks] == [3, 4]


def test_normalize_document_merges_short_heading_with_colon_list_item() -> None:
    for separator in ("：", ":"):
        heading = _block("label", BlockType.HEADING, "Content", right=40)
        heading.level = 4
        description = _block(
            "description",
            BlockType.LIST_ITEM,
            f"{separator}消息内容",
            left=40,
            right=100,
        )
        description.metadata.update({"ordered": False, "marker": "-", "indent_level": 0})

        result = normalize_document(_document([heading, description]))

        blocks = result.pages[0].blocks
        assert len(blocks) == 1
        assert blocks[0].type is BlockType.LIST_ITEM
        assert blocks[0].level is None
        assert blocks[0].text == f"Content{separator}消息内容"
        assert blocks[0].metadata["marker"] == "-"
        assert blocks[0].metadata["merged_inline_heading_label"] is True


def test_normalize_document_merges_inline_list_fragments() -> None:
    result = normalize_document(
        _document(
            [
                _block("role", BlockType.LIST_ITEM, "Role", right=40),
                _block("description", BlockType.LIST_ITEM, "：消息角色", left=40, right=90),
                _block("value", BlockType.PARAGRAPH, "system", left=90, right=125),
            ]
        )
    )
    blocks = result.pages[0].blocks
    assert len(blocks) == 1
    assert blocks[0].type is BlockType.LIST_ITEM
    assert blocks[0].text == "Role：消息角色 system"
    assert blocks[0].metadata["merged_block_ids"] == ["description", "value"]


def test_normalize_document_keeps_vertically_adjacent_list_items_separate() -> None:
    first = _block(
        "first",
        BlockType.LIST_ITEM,
        "简单直接，上手快",
        left=98,
        right=180,
        top=100,
        bottom=90,
    )
    second = _block(
        "second",
        BlockType.LIST_ITEM,
        "适合临时 demo",
        left=98,
        right=180,
        top=82,
        bottom=72,
    )

    result = normalize_document(_document([first, second]))

    assert [block.text for block in result.pages[0].blocks] == [
        "简单直接，上手快",
        "适合临时 demo",
    ]


def test_normalize_document_repairs_shifted_role_table_cells() -> None:
    table = _block(
        "role-table",
        BlockType.TABLE,
        "\n".join(
            [
                "| 角色 | 字典格式 | 对象格式 | 用途 | 示例 |",
                "| --- | --- | --- | --- | --- |",
                '| System {"role": "system", ...} | SystemMessage(...) | '
                "为、角色、规则 | 设定 AI 的行 | 示例 |",
                '| Assistant {"role": | "assistant", ...} | AIMessage(...) | AI 的回复 | 示例 |',
            ]
        ),
    )

    result = normalize_document(_document([table]))
    repaired = result.pages[0].blocks[0]

    assert (
        '| System | {"role": "system", ...} | SystemMessage(...) | '
        "设定 AI 的行为、角色、规则 | 示例 |"
    ) in repaired.text
    assert (
        '| Assistant | {"role": "assistant", ...} | AIMessage(...) | AI 的回复 | 示例 |'
    ) in repaired.text
    assert repaired.metadata["table_alignment_repaired_rows"] == ["System", "Assistant"]
