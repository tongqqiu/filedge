"""Tests for the fixed-width Parser pieces: slicer, validator, parser shim."""

import io

import pytest

from filedge.fixed_width import (
    FixedWidthLayoutError,
    FixedWidthLineTooShortError,
    FixedWidthParser,
    LayoutColumn,
    slice_line,
    validate_layout,
)


# --- slice_line: pure function ----------------------------------------------


def test_slice_line_extracts_single_column_at_start():
    layout = [LayoutColumn(name="account_number", start=1, width=10)]
    assert slice_line("ACME000123|extra", layout) == {"account_number": "ACME000123"}


def test_slice_line_is_one_indexed():
    # start=1 reads the first byte, not the second.
    layout = [LayoutColumn(name="flag", start=1, width=1)]
    assert slice_line("XABC", layout) == {"flag": "X"}


def test_slice_line_strips_whitespace_both_sides():
    layout = [LayoutColumn(name="amount", start=1, width=10)]
    assert slice_line("   12345   ", layout) == {"amount": "12345"}


def test_slice_line_extracts_multiple_adjacent_columns():
    layout = [
        LayoutColumn(name="account", start=1, width=4),
        LayoutColumn(name="branch", start=5, width=3),
    ]
    assert slice_line("ACME001", layout) == {"account": "ACME", "branch": "001"}


def test_slice_line_allows_gaps_between_columns():
    # Filler bytes between byte 5 and 9 are silently passed through.
    layout = [
        LayoutColumn(name="account", start=1, width=4),
        LayoutColumn(name="branch", start=9, width=3),
    ]
    assert slice_line("ACMEXXXX001", layout) == {"account": "ACME", "branch": "001"}


def test_slice_line_ignores_trailing_bytes():
    layout = [LayoutColumn(name="account", start=1, width=4)]
    assert slice_line("ACMEtrailing-garbage", layout) == {"account": "ACME"}


def test_slice_line_all_space_cell_becomes_empty_string():
    layout = [LayoutColumn(name="middle_name", start=1, width=10)]
    assert slice_line("          ", layout) == {"middle_name": ""}


def test_slice_line_extracts_column_at_end_of_line():
    layout = [LayoutColumn(name="status", start=9, width=2)]
    assert slice_line("BRANCH01OK", layout) == {"status": "OK"}


# --- validate_layout: pure function -----------------------------------------


def test_validate_layout_accepts_valid_sorted_layout():
    layout = [
        LayoutColumn(name="a", start=1, width=4),
        LayoutColumn(name="b", start=5, width=3),
    ]
    validate_layout(layout)  # no exception


def test_validate_layout_accepts_gaps_between_columns():
    layout = [
        LayoutColumn(name="a", start=1, width=4),
        LayoutColumn(name="b", start=9, width=3),
    ]
    validate_layout(layout)


def test_validate_layout_rejects_zero_width():
    layout = [LayoutColumn(name="a", start=1, width=0)]
    with pytest.raises(FixedWidthLayoutError, match="width"):
        validate_layout(layout)


def test_validate_layout_rejects_negative_width():
    layout = [LayoutColumn(name="a", start=1, width=-3)]
    with pytest.raises(FixedWidthLayoutError, match="width"):
        validate_layout(layout)


def test_validate_layout_rejects_zero_start():
    layout = [LayoutColumn(name="a", start=0, width=4)]
    with pytest.raises(FixedWidthLayoutError, match="start"):
        validate_layout(layout)


def test_validate_layout_rejects_negative_start():
    layout = [LayoutColumn(name="a", start=-1, width=4)]
    with pytest.raises(FixedWidthLayoutError, match="start"):
        validate_layout(layout)


def test_validate_layout_rejects_overlapping_pair():
    # account_number occupies 1-10, branch_code declared at 8-11 → overlaps at 8-10.
    layout = [
        LayoutColumn(name="account_number", start=1, width=10),
        LayoutColumn(name="branch_code", start=8, width=4),
    ]
    with pytest.raises(FixedWidthLayoutError) as exc_info:
        validate_layout(layout)
    message = str(exc_info.value)
    assert "account_number" in message
    assert "branch_code" in message
    assert "overlap" in message


def test_validate_layout_rejects_transitive_three_column_overlap():
    layout = [
        LayoutColumn(name="a", start=1, width=10),
        LayoutColumn(name="b", start=5, width=10),
        LayoutColumn(name="c", start=8, width=10),
    ]
    with pytest.raises(FixedWidthLayoutError, match="overlap"):
        validate_layout(layout)


def test_validate_layout_rejects_unsorted_columns():
    layout = [
        LayoutColumn(name="b", start=5, width=3),
        LayoutColumn(name="a", start=1, width=4),
    ]
    with pytest.raises(FixedWidthLayoutError) as exc_info:
        validate_layout(layout)
    assert "sorted" in str(exc_info.value).lower()


def test_validate_layout_accepts_adjacent_columns_with_no_gap():
    layout = [
        LayoutColumn(name="a", start=1, width=4),
        LayoutColumn(name="b", start=5, width=3),
    ]
    validate_layout(layout)


# --- FixedWidthParser -------------------------------------------------------


def test_parser_round_trips_a_small_file():
    parser = FixedWidthParser(
        layout=[
            LayoutColumn(name="account", start=1, width=4),
            LayoutColumn(name="amount", start=5, width=6),
        ]
    )
    f = io.StringIO("ACME000100\nFOOO002500\n")
    rows = list(parser.parse(f))
    assert rows == [
        {"account": "ACME", "amount": "000100"},
        {"account": "FOOO", "amount": "002500"},
    ]


def test_parser_skips_blank_and_whitespace_only_lines():
    parser = FixedWidthParser(
        layout=[LayoutColumn(name="account", start=1, width=4)]
    )
    f = io.StringIO("ACME\n\n   \nFOOO\n")
    rows = list(parser.parse(f))
    assert rows == [{"account": "ACME"}, {"account": "FOOO"}]


def test_parser_raises_on_short_line_with_actionable_message():
    parser = FixedWidthParser(
        layout=[
            LayoutColumn(name="account", start=1, width=10),
            LayoutColumn(name="amount", start=11, width=20),
        ]
    )
    f = io.StringIO("ACME000123truncated\n")  # 19 bytes, declared layout needs 30
    with pytest.raises(FixedWidthLineTooShortError) as exc_info:
        list(parser.parse(f))
    message = str(exc_info.value)
    assert "amount" in message
    assert "30" in message  # declared layout needs at least 30 bytes


def test_parser_ignores_trailing_bytes_on_long_line():
    parser = FixedWidthParser(layout=[LayoutColumn(name="account", start=1, width=4)])
    f = io.StringIO("ACMEtrailing-garbage-allowed\n")
    rows = list(parser.parse(f))
    assert rows == [{"account": "ACME"}]
