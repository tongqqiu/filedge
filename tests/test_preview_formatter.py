from filedge.preview_formatter import format_preview


def _rows(n=3):
    return [{"id": str(i), "name": f"Alice-{i}", "amount": "9.99"} for i in range(1, n + 1)]


def test_header_row_present():
    output = format_preview(_rows())
    assert "id" in output
    assert "name" in output
    assert "amount" in output


def test_row_numbers_start_at_one():
    output = format_preview(_rows())
    lines = output.splitlines()
    # skip header and separator
    assert lines[2].startswith("1")
    assert lines[3].startswith("2")


def test_values_appear_in_output():
    rows = [{"col": "hello"}]
    assert "hello" in format_preview(rows)


def test_cell_truncated_at_max_cell():
    long_val = "x" * 50
    rows = [{"col": long_val}]
    output = format_preview(rows, max_cell=30)
    for line in output.splitlines():
        # data cells must not exceed max_cell + some padding
        assert len(line) < 120


def test_truncated_cell_ends_with_ellipsis():
    rows = [{"col": "x" * 50}]
    output = format_preview(rows, max_cell=30)
    assert "…" in output


def test_overflow_columns_listed_below():
    # Force overflow: many wide columns in narrow width
    cols = {f"col_{i}": "val" for i in range(20)}
    rows = [cols]
    output = format_preview(rows, max_width=40)
    assert "not shown" in output


def test_no_overflow_when_columns_fit():
    rows = [{"id": "1", "name": "Alice"}]
    output = format_preview(rows, max_width=120)
    assert "not shown" not in output


def test_empty_rows_returns_placeholder():
    assert format_preview([]) == "(no rows)"


def test_none_values_render_as_empty():
    rows = [{"col": None}]
    output = format_preview(rows)
    assert output  # doesn't crash
    assert "None" not in output


def test_start_row_offset_shows_in_row_numbers():
    rows = _rows(3)
    output = format_preview(rows, start_row=42)
    lines = output.splitlines()
    assert lines[2].startswith("42")
    assert lines[3].startswith("43")
    assert lines[4].startswith("44")


def test_start_row_default_is_one():
    output = format_preview(_rows(2))
    lines = output.splitlines()
    assert lines[2].startswith("1")


def test_separator_line_present():
    output = format_preview(_rows())
    lines = output.splitlines()
    assert any(set(line.replace("-", "").replace("+", "").replace("|", "").strip()) == set() for line in lines)
