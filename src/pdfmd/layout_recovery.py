from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from pypdf import PdfReader

from pdfmd.code_analysis import infer_code_kind, infer_code_language
from pdfmd.image_code_recovery import recover_embedded_image_code
from pdfmd.models import BlockType, BoundingBox, DocumentBlock

_NUMBERED_LINE = re.compile(r"^(?P<indent>\s*)(?P<number>\d+)(?P<gap>[ \t]*)(?P<body>.*)$")
_TRAILING_NUMBER_RUN = re.compile(r"(?:\s+\d+){3,}\s*$")
_CODE_TYPES = {BlockType.CODE, BlockType.PARAGRAPH, BlockType.LIST_ITEM}
_CODE_KEYWORDS = re.compile(
    r"\b(?:async|await|class|const|def|elif|else|except|export|finally|for|from|function|"
    r"if|import|interface|lambda|let|npm|package|pip|print|python|raise|return|select|"
    r"try|var|while|with|yield)\b",
    flags=re.IGNORECASE,
)
_CODE_PUNCTUATION = re.compile(r"(?:==|!=|<=|>=|=>|->|:=|\+=|-=|\*=|/=|[(){}\[\]=:_`;])")


@dataclass(slots=True)
class LayoutRecoveryStats:
    pages_processed: int = 0
    candidates_found: int = 0
    unnumbered_candidates_found: int = 0
    blocks_recovered: int = 0
    fragments_merged: int = 0
    duplicate_fragments_removed: int = 0
    paragraphs_promoted: int = 0
    cross_page_continuations: int = 0
    line_number_tails_removed: int = 0
    layout_tables_recovered: int = 0
    image_candidates_found: int = 0
    embedded_images_found: int = 0
    image_ocr_pages: int = 0
    image_blocks_recovered: int = 0


@dataclass(slots=True)
class _LayoutCandidate:
    text: str
    start_line: int
    end_line: int
    source_start: int
    source_end: int
    confidence: float
    language: str
    source_kind: str = "numbered_gutter"


@dataclass(slots=True)
class _BlockGroup:
    indices: list[int]
    blocks: list[DocumentBlock]
    order: int

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text)

    @property
    def contains_code(self) -> bool:
        return any(block.type is BlockType.CODE for block in self.blocks)


def recover_code_layout(
    pdf_path: Path,
    blocks_by_page: dict[int, list[DocumentBlock]],
) -> LayoutRecoveryStats:
    """Recover code layout from a born-digital PDF's native text layer.

    The function mutates ``blocks_by_page`` in place and returns recovery statistics.
    Numbered snippets require a stable consecutive gutter. Unnumbered snippets require
    strongly code-like multiline layout and a close match to a parser block already
    classified as code (or an almost exact paragraph match). These conservative gates
    protect ordinary prose while restoring indentation that Docling can flatten.
    """

    stats = LayoutRecoveryStats()
    reader = PdfReader(str(pdf_path), strict=False)
    recovered_by_page: dict[int, list[DocumentBlock]] = {}

    for page_number, blocks in sorted(blocks_by_page.items()):
        if page_number < 1 or page_number > len(reader.pages):
            continue
        try:
            layout_text = reader.pages[page_number - 1].extract_text(extraction_mode="layout") or ""
        except Exception:
            continue

        stats.pages_processed += 1
        numbered_candidates = _find_layout_candidates(layout_text)
        unnumbered_candidates = _find_unnumbered_layout_candidates(layout_text)
        stats.candidates_found += len(numbered_candidates) + len(unnumbered_candidates)
        stats.unnumbered_candidates_found += len(unnumbered_candidates)
        if not blocks:
            continue

        if numbered_candidates:
            groups = _group_recoverable_blocks(blocks)
            matches = _match_candidates(numbered_candidates, groups)

            removed_indices: set[int] = set()
            page_recovered: list[DocumentBlock] = []
            for candidate_index, group_index in matches:
                candidate = numbered_candidates[candidate_index]
                group = groups[group_index]
                target = group.blocks[0]
                was_code = target.type is BlockType.CODE
                target.type = BlockType.CODE
                target.text = candidate.text
                target.bbox = _union_bbox(group.blocks)
                target.metadata.update(
                    {
                        "layout_recovered": True,
                        "layout_source": candidate.source_kind,
                        "start_line": candidate.start_line,
                        "end_line": candidate.end_line,
                        "layout_source_start": candidate.source_start,
                        "layout_source_end": candidate.source_end,
                        "layout_confidence": round(candidate.confidence, 3),
                    }
                )
                if candidate.language:
                    target.metadata["language"] = candidate.language
                    target.metadata["language_source"] = "layout_heuristic"
                code_kind = infer_code_kind(candidate.text)
                if code_kind:
                    target.metadata["code_kind"] = code_kind
                page_recovered.append(target)
                stats.blocks_recovered += 1
                if not was_code:
                    stats.paragraphs_promoted += 1
                if len(group.indices) > 1:
                    removed_indices.update(group.indices[1:])
                    stats.fragments_merged += len(group.indices) - 1

            if removed_indices:
                blocks[:] = [
                    block for index, block in enumerate(blocks) if index not in removed_indices
                ]
            if page_recovered:
                recovered_by_page[page_number] = page_recovered

        if unnumbered_candidates:
            recovered, promoted, removed = _recover_unnumbered_candidates(
                unnumbered_candidates,
                blocks,
            )
            stats.blocks_recovered += recovered
            stats.paragraphs_promoted += promoted
            stats.duplicate_fragments_removed += removed

        stats.layout_tables_recovered += _recover_layout_tables(layout_text, blocks)

    # A screenshot has no usable PDF text layer.  Run the OCR path only after
    # native recovery, and only for the remaining long single-line CODE items.
    # The helper is dependency/model guarded and leaves blocks untouched on any
    # optional-runtime failure.
    try:
        image_stats = recover_embedded_image_code(pdf_path, blocks_by_page)
        stats.image_candidates_found = image_stats.candidates_found
        stats.embedded_images_found = image_stats.embedded_images_found
        stats.image_ocr_pages = image_stats.ocr_pages
        stats.image_blocks_recovered = image_stats.blocks_recovered
    except Exception:
        # Optional OCR recovery must never make the primary Docling result fail.
        pass

    previous: DocumentBlock | None = None
    previous_page: int | None = None
    for page_number, recovered in sorted(recovered_by_page.items()):
        for block in recovered:
            start_line = _metadata_int(block, "start_line")
            previous_end = _metadata_int(previous, "end_line") if previous else None
            is_continuation = bool(block.metadata.get("continues_previous"))
            if (
                previous is not None
                and previous_page is not None
                and page_number == previous_page + 1
                and start_line is not None
                and previous_end is not None
                and start_line == previous_end + 1
            ):
                is_continuation = True
                block.metadata["continues_previous"] = True
            if is_continuation:
                stats.cross_page_continuations += 1
            previous = block
            previous_page = page_number

    return stats


def _find_layout_candidates(layout_text: str) -> list[_LayoutCandidate]:
    lines = (
        layout_text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").splitlines()
    )
    candidates: list[_LayoutCandidate] = []
    # Layout mode preserves large vertical gaps as blank lines.  Two or more
    # blank lines are a reliable block boundary, while an unnumbered line inside
    # a chunk is usually a visual wrap of a long code/output line.
    for segment in _layout_segments(lines):
        numbered = [
            (line_index, match)
            for line_index, line in segment
            if (match := _match_numbered_line(line)) is not None
        ]
        if not numbered:
            continue
        gutter_column = min(_number_column(match) for _, match in numbered)
        # Wrapped code may itself begin with a numeric literal farther to the
        # right (e.g. ``0},``).  Only the stable leftmost numeric column is the
        # line-number gutter.
        numbered = [
            (line_index, match)
            for line_index, match in numbered
            if _number_column(match) <= gutter_column + 2
        ]
        numbers = [int(match.group("number")) for _, match in numbered]
        if any(right != left + 1 for left, right in zip(numbers, numbers[1:], strict=False)):
            continue
        candidate = _candidate_from_segment(segment, numbered)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def _find_unnumbered_layout_candidates(layout_text: str) -> list[_LayoutCandidate]:
    """Return conservative code candidates whose source has no visible gutter."""

    lines = (
        layout_text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").splitlines()
    )
    candidates: list[_LayoutCandidate] = []
    for segment in _layout_segments(lines):
        # Numbered code has a stronger, dedicated recovery path.
        if any(_match_numbered_line(line) is not None for _, line in segment):
            continue
        segment_lines = [line for _, line in segment]
        if _aligned_table_rows(segment_lines) is not None:
            continue
        recovered_lines = _dedent_layout_lines(segment_lines)
        if not recovered_lines:
            continue
        confidence = _code_confidence(recovered_lines)
        nonempty_count = sum(bool(line.strip()) for line in recovered_lines)
        if confidence < 0.52 or (nonempty_count == 1 and confidence < 0.58):
            continue
        text = "\n".join(recovered_lines).rstrip()
        candidates.append(
            _LayoutCandidate(
                text=text,
                start_line=0,
                end_line=0,
                source_start=segment[0][0] + 1,
                source_end=segment[-1][0] + 1,
                confidence=confidence,
                language=infer_code_language(text),
                source_kind="unnumbered_text_layer",
            )
        )

    if not candidates:
        return []

    merged: list[_LayoutCandidate] = [candidates[0]]
    for candidate in candidates[1:]:
        previous = merged[-1]
        gap = candidate.source_start - previous.source_end
        languages_compatible = (
            not previous.language
            or not candidate.language
            or previous.language == candidate.language
        )
        if gap <= 3 and languages_compatible:
            separator = "\n\n" if gap > 1 else "\n"
            combined = f"{previous.text.rstrip()}{separator}{candidate.text.lstrip()}"
            previous.text = combined
            previous.source_end = candidate.source_end
            previous.confidence = max(previous.confidence, candidate.confidence)
            previous.language = infer_code_language(combined) or previous.language
        else:
            merged.append(candidate)
    return merged


def _dedent_layout_lines(lines: list[str]) -> list[str]:
    trimmed = [line.rstrip() for line in lines]
    nonempty = [line for line in trimmed if line.strip()]
    if not nonempty:
        return []
    common_indent = min(len(line) - len(line.lstrip()) for line in nonempty)
    return [line[common_indent:] if len(line) >= common_indent else "" for line in trimmed]


def _recover_unnumbered_candidates(
    candidates: list[_LayoutCandidate],
    blocks: list[DocumentBlock],
) -> tuple[int, int, int]:
    groups = [
        _BlockGroup(indices=[index], blocks=[block], order=order)
        for order, (index, block) in enumerate(
            entry for entry in enumerate(blocks) if entry[1].type in _CODE_TYPES
        )
    ]
    matches = _match_candidates(candidates, groups)
    recovered = 0
    promoted = 0
    matched_indices: set[int] = set()
    matched_candidates: list[_LayoutCandidate] = []

    for candidate_index, group_index in matches:
        candidate = candidates[candidate_index]
        group = groups[group_index]
        target = group.blocks[0]
        was_code = target.type is BlockType.CODE
        target.type = BlockType.CODE
        target.text = candidate.text
        target.metadata.update(
            {
                "layout_recovered": True,
                "layout_source": candidate.source_kind,
                "layout_source_start": candidate.source_start,
                "layout_source_end": candidate.source_end,
                "layout_confidence": round(candidate.confidence, 3),
            }
        )
        if candidate.language:
            target.metadata["language"] = candidate.language
            target.metadata["language_source"] = "layout_heuristic"
        code_kind = infer_code_kind(candidate.text)
        if code_kind:
            target.metadata["code_kind"] = code_kind
        matched_indices.add(group.indices[0])
        matched_candidates.append(candidate)
        recovered += 1
        promoted += int(not was_code)

    if not matched_candidates:
        return 0, 0, 0

    removed_indices: set[int] = set()
    for index, block in enumerate(blocks):
        if index in matched_indices or block.type not in {
            BlockType.CODE,
            BlockType.PARAGRAPH,
        }:
            continue
        if block.type is not BlockType.CODE and not _is_adjacent_duplicate_block(
            index,
            block.text,
            matched_indices,
            matched_candidates,
        ):
            continue
        original = block.text
        cleaned = original
        for candidate in matched_candidates:
            cleaned = _remove_duplicate_code_lines(cleaned, candidate.text)
        cleaned = cleaned.strip()
        if cleaned == original.strip():
            continue
        if not cleaned:
            removed_indices.add(index)
            continue
        block.text = cleaned
        block.metadata["layout_duplicate_removed"] = True
        if block.type is BlockType.CODE and _code_confidence(cleaned.splitlines()) < 0.42:
            block.type = BlockType.PARAGRAPH
            block.metadata["demoted_after_layout_recovery"] = True

    if removed_indices:
        blocks[:] = [
            block for index, block in enumerate(blocks) if index not in removed_indices
        ]
    return recovered, promoted, len(removed_indices)


def _is_adjacent_duplicate_block(
    index: int,
    text: str,
    matched_indices: set[int],
    candidates: list[_LayoutCandidate],
) -> bool:
    if not any(abs(index - matched_index) == 1 for matched_index in matched_indices):
        return False
    normalized_block = _normalize_for_match(text)
    if not normalized_block:
        return False
    for candidate in candidates:
        for line in candidate.text.splitlines():
            normalized_line = _normalize_for_match(line)
            if (
                len(normalized_line) >= 16
                and normalized_line in normalized_block
                and len(normalized_line) / len(normalized_block) >= 0.85
            ):
                return True
    return False


def _remove_duplicate_code_lines(text: str, recovered_text: str) -> str:
    cleaned = text
    candidate_lines = sorted(
        (
            line.strip()
            for line in recovered_text.splitlines()
            if len(_normalize_for_match(line)) >= 16
        ),
        key=len,
        reverse=True,
    )
    for line in candidate_lines:
        tokens = line.split()
        if not tokens:
            continue
        pattern = r"\s+".join(re.escape(token) for token in tokens)
        cleaned = re.sub(pattern, " ", cleaned, count=1, flags=re.IGNORECASE)
    return cleaned


def _recover_layout_tables(layout_text: str, blocks: list[DocumentBlock]) -> int:
    lines = (
        layout_text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").splitlines()
    )
    recovered = 0
    used_blocks: set[int] = set()
    for segment in _layout_segments(lines):
        if any(_match_numbered_line(line) is not None for _, line in segment):
            continue
        rows = _aligned_table_rows([line for _, line in segment])
        if rows is None:
            continue
        candidate_text = " ".join(cell for row in rows for cell in row)
        normalized_candidate = _normalize_for_match(candidate_text)
        best: tuple[float, int] | None = None
        for index, block in enumerate(blocks):
            is_repair_target = block.type is BlockType.CODE or (
                block.type is BlockType.PARAGRAPH
                and bool(block.metadata.get("demoted_after_layout_recovery"))
            )
            if index in used_blocks or not is_repair_target:
                continue
            normalized_block = _normalize_for_match(block.text)
            if not normalized_block:
                continue
            similarity = SequenceMatcher(
                None,
                normalized_candidate,
                normalized_block,
                autojunk=False,
            ).ratio()
            containment = min(len(normalized_candidate), len(normalized_block)) / max(
                len(normalized_candidate), len(normalized_block)
            )
            if similarity < 0.9 or containment < 0.85:
                continue
            if best is None or similarity > best[0]:
                best = (similarity, index)
        if best is None:
            continue
        block = blocks[best[1]]
        escaped_rows = [[cell.replace("|", r"\|") for cell in row] for row in rows]
        separator = ["---"] * len(escaped_rows[0])
        table_rows = [escaped_rows[0], separator, *escaped_rows[1:]]
        block.type = BlockType.TABLE
        block.text = "\n".join(f"| {' | '.join(row)} |" for row in table_rows)
        block.metadata.update(
            {
                "layout_table_recovered": True,
                "table_rows": len(escaped_rows),
                "table_columns": len(escaped_rows[0]),
                "table_serialization": "gfm",
            }
        )
        used_blocks.add(best[1])
        recovered += 1
    return recovered


def _aligned_table_rows(lines: list[str]) -> list[list[str]] | None:
    nonempty_lines = [line.strip() for line in lines if line.strip()]
    rows = [
        [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
        for line in nonempty_lines
    ]
    # This is a repair path for content Docling mislabeled as code. Requiring
    # three aligned columns is intentionally conservative; ordinary two-column
    # prose is more common than a genuinely missed two-column table.
    if len(rows) < 3 or len(rows[0]) < 3:
        return None
    column_count = len(rows[0])
    if any(len(row) != column_count for row in rows):
        return None
    if any(
        re.search(
            r"(?:[(){}\[\]=;]|^\s*(?:if|for|while|def|class|return|import|from)\b)",
            line,
            flags=re.IGNORECASE,
        )
        for line in nonempty_lines
    ):
        return None
    # Require an actual interior alignment gap on every row; indentation alone
    # must never turn source code into a table.
    gap_ends = [
        [match.end() for match in re.finditer(r"(?<=\S)\s{2,}(?=\S)", line)]
        for line in nonempty_lines
    ]
    if any(len(positions) != column_count - 1 for positions in gap_ends):
        return None
    for column in range(column_count - 1):
        starts = [positions[column] for positions in gap_ends]
        if max(starts) - min(starts) > 3:
            return None
    if any(len(re.findall(r"\S\s{2,}\S", line)) < column_count - 1 for line in nonempty_lines):
        return None
    return rows


def _layout_segments(lines: list[str]) -> list[list[tuple[int, str]]]:
    segments: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if line.strip():
            current.append((index, line))
        elif current:
            # A blank source-code line still contains its gutter number.  A truly
            # empty layout line therefore separates visual blocks and must not be
            # copied into the preceding code candidate with nearby headings.
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def _match_numbered_line(line: str) -> re.Match[str] | None:
    match = _NUMBERED_LINE.match(line)
    if match is None:
        return None
    # A blank code line can render as a bare gutter number.  A number immediately
    # followed by other text (for example a wrapped ``0},``) is not a gutter.
    if not match.group("indent") and not match.group("gap") and match.group("body"):
        return None
    return match


def _candidate_from_segment(
    segment: list[tuple[int, str]], numbered: list[tuple[int, re.Match[str]]]
) -> _LayoutCandidate | None:
    matches_by_line = {line_index: match for line_index, match in numbered}
    payload_columns = [match.start("body") for _, match in numbered if match.group("body").strip()]
    # A visually wrapped continuation can start to the left of every numbered
    # payload on a continuation page.  Ignoring that column caused the fixed
    # slice below to delete real source characters (for example ``["fu`` from
    # ``["function"]``).  Marker uses the visual left edge of all code lines;
    # apply the same principle while retaining our pypdf layout fallback.
    payload_columns.extend(
        len(raw_line) - len(raw_line.lstrip())
        for line_index, raw_line in segment
        if line_index not in matches_by_line and raw_line.strip()
    )
    if not payload_columns:
        return None
    # The smallest payload column represents the code area's left edge. Lines that
    # begin farther right retain that delta as code indentation.
    code_column = min(payload_columns)
    recovered_lines: list[str] = []
    for line_index, raw_line in segment:
        match = matches_by_line.get(line_index)
        if match is not None:
            payload = raw_line[code_column:] if len(raw_line) > code_column else ""
        else:
            # A non-empty layout line without a gutter number is a visual wrap of
            # the preceding logical source line.  Joining it prevents page-width
            # wrapping from introducing a syntax-changing newline into code.
            payload = raw_line[code_column:] if raw_line.strip() else ""
        payload = payload.rstrip()
        if match is None:
            if payload.strip() and recovered_lines:
                recovered_lines[-1] = _join_visual_continuation(
                    recovered_lines[-1], payload.lstrip()
                )
            continue
        recovered_lines.append(payload)

    confidence = _code_confidence(recovered_lines)
    # Keep low-confidence numbered output blocks as candidates; they are only
    # allowed to replace an item Docling already classified as code.  Promoting
    # prose/list blocks still requires the higher threshold in the matcher.
    if confidence < 0.15:
        return None

    text = "\n".join(recovered_lines).rstrip()
    if not text:
        return None
    return _LayoutCandidate(
        text=text,
        start_line=int(numbered[0][1].group("number")),
        end_line=int(numbered[-1][1].group("number")),
        source_start=segment[0][0] + 1,
        source_end=segment[-1][0] + 1,
        confidence=confidence,
        language=infer_code_language(text),
    )


def _code_confidence(lines: list[str]) -> float:
    nonempty = [line for line in lines if line.strip()]
    if not nonempty:
        return 0.0
    joined = "\n".join(nonempty)
    keyword_hits = len(_CODE_KEYWORDS.findall(joined))
    punctuation_hits = len(_CODE_PUNCTUATION.findall(joined))
    indented_lines = sum(bool(line[:1].isspace()) for line in nonempty)
    code_lines = sum(
        bool(_CODE_KEYWORDS.search(line) or _CODE_PUNCTUATION.search(line)) for line in nonempty
    )
    score = 0.15
    score += min(0.25, keyword_hits * 0.08)
    score += min(0.3, punctuation_hits * 0.03)
    score += min(0.25, code_lines / len(nonempty) * 0.25)
    score += min(0.1, indented_lines / len(nonempty) * 0.15)
    return min(1.0, score)


def _join_visual_continuation(first: str, second: str) -> str:
    left = first.rstrip()
    right = second.lstrip()
    if not left:
        return right
    if not right:
        return left
    # Layout wrapping can remove an ordinary token separator. Preserve it only
    # for adjacent ASCII word characters; punctuation and CJK wraps concatenate.
    separator = " " if _is_ascii_word(left[-1]) and _is_ascii_word(right[0]) else ""
    return f"{left}{separator}{right}"


def _is_ascii_word(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character == "_")


def _number_column(match: re.Match[str]) -> int:
    return match.start("number")


def _group_recoverable_blocks(blocks: list[DocumentBlock]) -> list[_BlockGroup]:
    recoverable = [
        (index, block) for index, block in enumerate(blocks) if block.type in _CODE_TYPES
    ]
    if not recoverable:
        return []

    parents = list(range(len(recoverable)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    # Docling occasionally emits closing quote/bracket spans at the end of its
    # traversal even though their bboxes overlap an earlier code item.  Group by
    # geometry globally rather than requiring the fragments to be adjacent.
    for left in range(len(recoverable)):
        for right in range(left):
            if _should_merge_fragments(recoverable[left][1], recoverable[right][1]):
                union(left, right)

    components: dict[int, list[tuple[int, DocumentBlock]]] = {}
    for position, entry in enumerate(recoverable):
        components.setdefault(find(position), []).append(entry)

    groups = [
        _BlockGroup(
            indices=[index for index, _ in sorted(entries)],
            blocks=[block for _, block in sorted(entries)],
            order=min(index for index, _ in entries),
        )
        for entries in components.values()
    ]
    groups.sort(key=lambda group: group.order)
    for order, group in enumerate(groups):
        group.order = order
    return groups


def _should_merge_fragments(first: DocumentBlock, second: DocumentBlock) -> bool:
    if first.bbox is None or second.bbox is None:
        return False
    if first.type is not BlockType.CODE and second.type is not BlockType.CODE:
        return False
    first_low, first_high = sorted((first.bbox.top, first.bbox.bottom))
    second_low, second_high = sorted((second.bbox.top, second.bbox.bottom))
    overlap = max(0.0, min(first_high, second_high) - max(first_low, second_low))
    smaller_height = max(1.0, min(first_high - first_low, second_high - second_low))
    horizontal_overlap = max(
        0.0,
        min(first.bbox.right, second.bbox.right) - max(first.bbox.left, second.bbox.left),
    )
    horizontal_gap = max(
        0.0,
        max(first.bbox.left, second.bbox.left) - min(first.bbox.right, second.bbox.right),
    )
    return overlap / smaller_height >= 0.25 and (horizontal_overlap > 0 or horizontal_gap <= 8)


def _match_candidates(
    candidates: list[_LayoutCandidate], groups: list[_BlockGroup]
) -> list[tuple[int, int]]:
    possible: list[tuple[float, float, int, int]] = []
    denominator = max(1, max(len(candidates), len(groups)) - 1)
    for candidate_index, candidate in enumerate(candidates):
        candidate_text = _normalize_for_match(candidate.text)
        for group_index, group in enumerate(groups):
            if not group.contains_code and candidate.confidence < 0.42:
                continue
            group_text = _normalize_for_match(_strip_docling_line_number_tail(group.text))
            if not candidate_text or not group_text:
                continue
            similarity = SequenceMatcher(None, candidate_text, group_text, autojunk=False).ratio()
            containment = min(len(candidate_text), len(group_text)) / max(
                len(candidate_text), len(group_text)
            )
            order_bonus = max(0.0, 0.08 - abs(candidate_index - group.order) / denominator * 0.08)
            score = similarity + order_bonus
            threshold = 0.48 if group.contains_code else 0.78
            if similarity >= threshold and containment >= (0.35 if group.contains_code else 0.65):
                possible.append((score, similarity, candidate_index, group_index))

    matches: list[tuple[int, int]] = []
    used_candidates: set[int] = set()
    used_groups: set[int] = set()
    for _, _, candidate_index, group_index in sorted(possible, reverse=True):
        if candidate_index in used_candidates or group_index in used_groups:
            continue
        used_candidates.add(candidate_index)
        used_groups.add(group_index)
        matches.append((candidate_index, group_index))
    return sorted(matches)


def _strip_docling_line_number_tail(text: str) -> str:
    match = _TRAILING_NUMBER_RUN.search(text)
    if match is None:
        return text
    tokens = list(re.finditer(r"\d+", match.group()))
    numbers = [int(token.group()) for token in tokens]
    suffix_start = len(numbers) - 1
    while suffix_start > 0 and numbers[suffix_start] == numbers[suffix_start - 1] + 1:
        suffix_start -= 1
    if len(numbers) - suffix_start < 3:
        return text
    cut_at = match.start() + tokens[suffix_start].start()
    return text[:cut_at].rstrip()


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _union_bbox(blocks: list[DocumentBlock]) -> BoundingBox | None:
    boxes = [block.bbox for block in blocks if block.bbox is not None]
    if not boxes:
        return None
    return BoundingBox(
        left=min(box.left for box in boxes),
        top=max(box.top for box in boxes),
        right=max(box.right for box in boxes),
        bottom=min(box.bottom for box in boxes),
    )


def _metadata_int(block: DocumentBlock | None, key: str) -> int | None:
    if block is None:
        return None
    value = block.metadata.get(key)
    return value if isinstance(value, int) else None
