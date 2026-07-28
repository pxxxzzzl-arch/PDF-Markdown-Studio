from __future__ import annotations

import re

from pdfmd.models import BlockType, BoundingBox, DocumentBlock, ParsedDocument
from pdfmd.table_utils import parse_gfm_rows, render_gfm_rows

_CJK_SPACE_RE = re.compile(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])")
_CHAPTER_RE = re.compile(r"^第\s*\d+\s*章")
_DECIMAL_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)(?:\s|$)")
_MAJOR_HEADING_RE = re.compile(r"^\d+\s*[、．](?!\d)")
_PROMOTABLE_CAPTION_RE = re.compile(r"^(?:方法|方式|举例|类型|格式)\s*\d+\s*[：:]")
_SUBHEADING_RE = re.compile(
    r"^(?:方法|方式|举例|类型|格式|输出|源码|问题|优点|缺点|开发建议|使用场景|基本概念)"
)
_LIST_CONTINUATION_PREFIX_RE = re.compile(r"^(?:[①-⑳]|\d+\s*[、.)]|[^：:]{1,20}[：:])")


def normalize_document(document: ParsedDocument) -> ParsedDocument:
    """Apply deterministic structure repairs shared by every parsing engine."""

    normalized = document.model_copy(deep=True)
    current_section_level = 2
    merged_fragments = 0
    promoted_captions = 0

    for page in normalized.pages:
        page.blocks, merged = _merge_inline_fragments(page.blocks)
        merged_fragments += merged
        for block in page.blocks:
            if block.type is BlockType.TABLE:
                _repair_shifted_role_table(block)
            if block.type not in {BlockType.CODE, BlockType.RAW_MARKDOWN}:
                block.text = normalize_prose_spacing(block.text)
                if block.table_html:
                    block.table_html = normalize_prose_spacing(block.table_html)

            if block.type is BlockType.CAPTION and _PROMOTABLE_CAPTION_RE.match(block.text):
                block.type = BlockType.HEADING
                block.level = min(6, current_section_level + 1)
                block.metadata["promoted_from_caption"] = True
                promoted_captions += 1

            if block.type is not BlockType.HEADING:
                continue
            inferred = _inferred_heading_level(
                block.text,
                semantic_level=_metadata_int(block, "semantic_level"),
            )
            if inferred is not None:
                block.level = inferred
                current_section_level = inferred
            elif _SUBHEADING_RE.match(block.text):
                block.level = min(6, current_section_level + 1)
            else:
                # Docling can assign deeply nested levels to visual emphasis even
                # when no intermediate headings exist.  Keep a genuine shallower
                # level, but never let an unnumbered heading jump more than one
                # level below the current numbered section.
                upstream_level = min(6, max(2, block.level or current_section_level))
                block.level = min(upstream_level, current_section_level + 1)

    normalized.metadata["postprocessing"] = {
        "inline_fragments_merged": merged_fragments,
        "captions_promoted": promoted_captions,
    }
    return normalized


def normalize_prose_spacing(text: str) -> str:
    return _CJK_SPACE_RE.sub("", text)


def _inferred_heading_level(text: str, *, semantic_level: int | None = None) -> int | None:
    compact = re.sub(r"\s+", " ", text).strip()
    if _CHAPTER_RE.match(compact):
        return 1
    decimal = _DECIMAL_HEADING_RE.match(compact)
    if decimal:
        # The document title occupies H1; 1.1 -> H3 and 1.5.1 -> H4.
        return min(6, decimal.group(1).count(".") + 2)
    if _MAJOR_HEADING_RE.match(compact):
        # A Chinese enumerated label such as ``1、系统消息`` can either be a
        # document-level section or a deeply nested sibling inside ``1.2``.
        # Docling's hierarchy signal is stronger than the surface numbering in
        # the latter case, so never flatten a confidently deep heading to H2.
        if semantic_level is not None and semantic_level >= 4:
            return None
        return 2
    return None


def _merge_inline_fragments(
    blocks: list[DocumentBlock],
) -> tuple[list[DocumentBlock], int]:
    result: list[DocumentBlock] = []
    merged = 0
    for incoming in blocks:
        block = incoming.model_copy(deep=True)
        if result and _same_visual_line(result[-1], block) and _can_merge_inline(result[-1], block):
            previous = result[-1]
            heading_label = _is_inline_heading_list_label(previous, block)
            previous.text = _join_fragments(previous.text, block.text)
            previous.bbox = _union_bbox(previous.bbox, block.bbox)
            previous.metadata.setdefault("merged_block_ids", []).append(block.id)
            if heading_label:
                previous.type = BlockType.LIST_ITEM
                previous.level = None
                for key in ("ordered", "marker", "indent_level"):
                    if key in block.metadata:
                        previous.metadata[key] = block.metadata[key]
                previous.metadata["merged_inline_heading_label"] = True
            merged += 1
            continue

        if result and _is_wrapped_list_continuation(result[-1], block):
            previous = result[-1]
            previous.text = _join_fragments(previous.text, block.text)
            previous.bbox = _union_bbox(previous.bbox, block.bbox)
            previous.metadata.setdefault("merged_block_ids", []).append(block.id)
            merged += 1
            continue

        # A paragraph aligned with the list text column, directly after another
        # bullet, is commonly a bullet whose marker span was missed by Docling.
        if result and _looks_like_missed_list_item(result[-1], block):
            block.type = BlockType.LIST_ITEM
            block.metadata.setdefault("ordered", False)
            block.metadata.setdefault("marker", "-")
            block.metadata["promoted_from_paragraph"] = True
        result.append(block)
    return result, merged


def _same_visual_line(first: DocumentBlock, second: DocumentBlock) -> bool:
    if first.bbox is None or second.bbox is None or first.page != second.page:
        return False
    overlap = max(
        0.0,
        min(first.bbox.top, second.bbox.top) - max(first.bbox.bottom, second.bbox.bottom),
    )
    height = min(
        max(0.01, first.bbox.top - first.bbox.bottom),
        max(0.01, second.bbox.top - second.bbox.bottom),
    )
    horizontal_gap = second.bbox.left - first.bbox.right
    return overlap / height >= 0.65 and -2 <= horizontal_gap <= 18


def _can_merge_inline(first: DocumentBlock, second: DocumentBlock) -> bool:
    allowed = {BlockType.LIST_ITEM, BlockType.PARAGRAPH}
    return (first.type is BlockType.LIST_ITEM and second.type in allowed) or (
        _is_inline_heading_list_label(first, second)
    )


def _is_inline_heading_list_label(first: DocumentBlock, second: DocumentBlock) -> bool:
    label = first.text.strip()
    return bool(
        first.type is BlockType.HEADING
        and second.type is BlockType.LIST_ITEM
        and 0 < len(label) <= 40
        and "\n" not in label
        and second.text.lstrip().startswith(("：", ":"))
    )


def _metadata_int(block: DocumentBlock, key: str) -> int | None:
    value = block.metadata.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_wrapped_list_continuation(first: DocumentBlock, second: DocumentBlock) -> bool:
    # Two upstream ListItem objects are already separate semantic bullets.  Only
    # merge a following paragraph whose bullet marker was not recognized.
    if first.type is not BlockType.LIST_ITEM or second.type is not BlockType.PARAGRAPH:
        return False
    if first.bbox is None or second.bbox is None or first.page != second.page:
        return False
    if first.text.rstrip().endswith(("。", "！", "？", ".", "!", "?", ";", "；")):
        return False
    if _LIST_CONTINUATION_PREFIX_RE.match(second.text.strip()):
        return False
    vertical_gap = first.bbox.bottom - second.bbox.top
    left_delta = abs(first.bbox.left - second.bbox.left)
    return -2 <= vertical_gap <= 22 and left_delta <= 8


def _looks_like_missed_list_item(first: DocumentBlock, second: DocumentBlock) -> bool:
    if first.type is not BlockType.LIST_ITEM or second.type is not BlockType.PARAGRAPH:
        return False
    if first.bbox is None or second.bbox is None or first.page != second.page:
        return False
    vertical_gap = first.bbox.bottom - second.bbox.top
    return (
        0 <= vertical_gap <= 22
        and abs(first.bbox.left - second.bbox.left) <= 4
        and bool(re.match(r"^.{1,24}\s*[：:]", second.text.strip()))
    )


def _join_fragments(first: str, second: str) -> str:
    left = first.rstrip()
    right = second.lstrip()
    if not left:
        return right
    if not right:
        return left
    if right.startswith(tuple("，。；：！？、,.!?;:)]}）】》")):
        return f"{left}{right}"
    if left.endswith(("(", "[", "{", "（", "【", "《")):
        return f"{left}{right}"
    return f"{left} {right}"


def _union_bbox(first: BoundingBox | None, second: BoundingBox | None) -> BoundingBox | None:
    if first is None:
        return second
    if second is None:
        return first
    return BoundingBox(
        left=min(first.left, second.left),
        top=max(first.top, second.top),
        right=max(first.right, second.right),
        bottom=min(first.bottom, second.bottom),
    )


def _repair_shifted_role_table(block: DocumentBlock) -> None:
    """Repair a common Docling cell shift in role/dict/object comparison tables."""

    if block.table_html or not block.text:
        return
    rows = parse_gfm_rows(block.text)
    if not rows or len(rows) < 3 or len(rows[0]) != 5:
        return
    headers = [cell.replace(" ", "") for cell in rows[0]]
    if "角色" not in headers[0] or "字典" not in headers[1] or "对象" not in headers[2]:
        return

    repaired_rows: list[str] = []
    role_pattern = re.compile(r"^(System|User|Assistant|Tool)\s+(\{.*)$", re.IGNORECASE)
    for row_index in range(2, len(rows)):
        cells = rows[row_index]
        if len(cells) != 5 or not (match := role_pattern.match(cells[0].strip())):
            continue
        message_index = next(
            (
                index
                for index, cell in enumerate(cells[1:], start=1)
                if re.search(r"\b[A-Za-z]*Message\s*\(", cell)
            ),
            None,
        )
        if message_index is None:
            continue
        dictionary_value = " ".join([match.group(2), *cells[1:message_index]]).strip()
        tail = cells[message_index + 1 :]
        if len(tail) < 2:
            continue
        purpose_parts = tail[:-1]
        if (
            len(purpose_parts) == 2
            and purpose_parts[1].startswith("设定")
            and purpose_parts[0].startswith("为")
        ):
            purpose = f"{purpose_parts[1]}{purpose_parts[0]}"
        else:
            purpose = " ".join(purpose_parts)
        rows[row_index] = [
            match.group(1),
            dictionary_value,
            cells[message_index],
            purpose,
            tail[-1],
        ]
        repaired_rows.append(match.group(1))

    if repaired_rows:
        block.text = render_gfm_rows(rows)
        block.metadata["table_alignment_repaired"] = True
        block.metadata["table_alignment_repaired_rows"] = repaired_rows
