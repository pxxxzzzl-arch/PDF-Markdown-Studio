from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True, slots=True)
class GfmTableShape:
    rows: int
    columns: tuple[int, ...]
    separator_index: int | None

    @property
    def is_structurally_valid(self) -> bool:
        return (
            self.rows >= 1
            and self.separator_index == 1
            and bool(self.columns)
            and len(set(self.columns)) == 1
            and self.columns[0] > 0
        )


def inspect_gfm_table(markdown: str) -> GfmTableShape:
    """Return the row/column shape of a single GFM table.

    Pipes escaped with a backslash remain cell content. Unescaped pipes always
    split cells, including inside inline code, which follows GFM table parsing.
    """

    rows: list[list[str]] = []
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_row(line)
        if cells is None:
            return GfmTableShape(rows=0, columns=(), separator_index=None)
        rows.append(cells)

    separator_index = next(
        (
            index
            for index, cells in enumerate(rows)
            if cells and all(_SEPARATOR_CELL.fullmatch(cell.replace(" ", "")) for cell in cells)
        ),
        None,
    )
    content_rows = len(rows) - (1 if separator_index is not None else 0)
    return GfmTableShape(
        rows=max(0, content_rows),
        columns=tuple(len(cells) for cells in rows),
        separator_index=separator_index,
    )


def parse_gfm_rows(markdown: str) -> list[list[str]] | None:
    """Parse every GFM row, including the separator row, without unescaping cells."""

    rows: list[list[str]] = []
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_row(line)
        if cells is None:
            return None
        rows.append(cells)
    return rows or None


def render_gfm_rows(rows: list[list[str]]) -> str:
    return "\n".join(f"| {' | '.join(cell.strip() for cell in row)} |" for row in rows)


def is_valid_gfm_table(
    markdown: str,
    *,
    expected_rows: int | None = None,
    expected_columns: int | None = None,
) -> bool:
    shape = inspect_gfm_table(markdown)
    if not shape.is_structurally_valid:
        return False
    if expected_rows is not None and shape.rows != expected_rows:
        return False
    if expected_columns is not None and any(
        column_count != expected_columns for column_count in shape.columns
    ):
        return False
    return True


def _split_row(line: str) -> list[str] | None:
    if "|" not in line:
        return None

    cells: list[str] = []
    buffer: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            buffer.append(character)
            escaped = False
            continue
        if character == "\\":
            buffer.append(character)
            escaped = True
            continue
        if character == "|":
            cells.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(character)
    cells.append("".join(buffer).strip())

    if cells and cells[0] == "":
        cells.pop(0)
    if cells and cells[-1] == "":
        cells.pop()
    return cells or None
