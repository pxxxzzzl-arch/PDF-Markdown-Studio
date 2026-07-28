from pdfmd.table_utils import inspect_gfm_table, is_valid_gfm_table


def test_gfm_table_shape_counts_content_rows_and_columns() -> None:
    markdown = "| Name | Value |\n| :--- | ---: |\n| alpha | 1 |"
    shape = inspect_gfm_table(markdown)
    assert shape.rows == 2
    assert shape.columns == (2, 2, 2)
    assert shape.separator_index == 1
    assert is_valid_gfm_table(markdown, expected_rows=2, expected_columns=2)


def test_gfm_table_accepts_escaped_pipe_but_rejects_unescaped_extra_column() -> None:
    escaped = "| Kind | Payload |\n| --- | --- |\n| JSON | a \\| b |"
    broken = "| Kind | Payload |\n| --- | --- |\n| JSON | a | b |"

    assert is_valid_gfm_table(escaped, expected_rows=2, expected_columns=2)
    assert not is_valid_gfm_table(broken, expected_rows=2, expected_columns=2)


def test_gfm_table_rejects_missing_separator_or_wrong_row_count() -> None:
    missing_separator = "| A | B |\n| one | two |"
    valid = "| A | B |\n| --- | --- |\n| one | two |"

    assert not is_valid_gfm_table(missing_separator)
    assert not is_valid_gfm_table(valid, expected_rows=3, expected_columns=2)
