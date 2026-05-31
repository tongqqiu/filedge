"""The re-drop transform turns a quarantine sidecar (ADR-0019) back into a clean,
re-droppable NDJSON File: each line's source ``row`` unwrapped, the
row_number/column/error diagnostics stripped, and a malformed sidecar failing
loudly rather than emitting a partial File.
"""

import io
import json

import pytest

from filedge.parser import NDJSONParser
from filedge.quarantine.redrop import (
    MalformedSidecarError,
    read_quarantined_rows,
    redrop_quarantine,
)


def _sidecar(*records: dict) -> io.StringIO:
    return io.StringIO("".join(json.dumps(r) + "\n" for r in records))


def test_unwraps_row_and_strips_diagnostics():
    sidecar = _sidecar(
        {"row_number": 2, "column": "amount", "error": "not a float",
         "row": {"id": "2", "amount": "n/a"}},
        {"row_number": 5, "column": "id", "error": "not an integer",
         "row": {"id": "x", "amount": "3.5"}},
    )

    rows = list(read_quarantined_rows(sidecar))

    assert rows == [{"id": "2", "amount": "n/a"}, {"id": "x", "amount": "3.5"}]
    # Diagnostic fields never leak into the unwrapped rows.
    for row in rows:
        assert "row_number" not in row
        assert "column" not in row
        assert "error" not in row


def test_redrop_writes_ndjson_and_returns_count():
    sidecar = _sidecar(
        {"row_number": 1, "column": "amount", "error": "bad", "row": {"id": "1", "amount": "n/a"}},
        {"row_number": 2, "column": "amount", "error": "bad", "row": {"id": "2", "amount": ""}},
    )
    out = io.StringIO()

    count = redrop_quarantine(sidecar, out)

    assert count == 2
    lines = out.getvalue().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"id": "1", "amount": "n/a"},
        {"id": "2", "amount": ""},
    ]


def test_empty_sidecar_yields_empty_output():
    out = io.StringIO()
    count = redrop_quarantine(io.StringIO(""), out)
    assert count == 0
    assert out.getvalue() == ""


def test_blank_lines_are_skipped():
    sidecar = io.StringIO(
        json.dumps({"row_number": 1, "column": "a", "error": "e", "row": {"a": "1"}}) + "\n"
        "\n"
        "   \n"
        + json.dumps({"row_number": 2, "column": "a", "error": "e", "row": {"a": "2"}}) + "\n"
    )
    out = io.StringIO()
    assert redrop_quarantine(sidecar, out) == 2


def test_invalid_json_line_raises():
    sidecar = io.StringIO("{not valid json}\n")
    with pytest.raises(MalformedSidecarError, match="line 1"):
        list(read_quarantined_rows(sidecar))


def test_line_missing_row_field_raises():
    sidecar = _sidecar({"row_number": 1, "column": "a", "error": "e"})
    with pytest.raises(MalformedSidecarError, match="no 'row'"):
        list(read_quarantined_rows(sidecar))


def test_line_with_non_object_row_raises():
    sidecar = _sidecar({"row_number": 1, "column": "a", "error": "e", "row": "just a string"})
    with pytest.raises(MalformedSidecarError, match="not a JSON object"):
        list(read_quarantined_rows(sidecar))


def test_redrop_output_round_trips_through_ndjson_parser():
    source_rows = [{"id": "1", "amount": "1.50"}, {"id": "2", "amount": "9.99"}]
    sidecar = _sidecar(
        *({"row_number": i, "column": "amount", "error": "bad", "row": row}
          for i, row in enumerate(source_rows, start=1))
    )
    out = io.StringIO()

    redrop_quarantine(sidecar, out)

    reparsed = list(NDJSONParser().parse(io.StringIO(out.getvalue())))
    assert reparsed == source_rows
