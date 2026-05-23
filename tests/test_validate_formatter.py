import json

from filedge.validate_formatter import format_json, format_text
from filedge.validator import RowFailure, ValidationResult


def _result(rows_checked=0, failures=None, undeclared=None):
    return ValidationResult(
        rows_checked=rows_checked,
        failures=failures or [],
        undeclared_columns=undeclared or [],
    )


def test_format_text_with_failures():
    result = _result(
        rows_checked=10,
        failures=[
            RowFailure(row_number=3, column="amount", error="cannot coerce 'bad' to float"),
            RowFailure(row_number=7, column="count", error="cannot coerce 'x' to integer"),
        ],
    )
    text = format_text(result)
    assert "row 3" in text
    assert "amount" in text
    assert "row 7" in text
    assert "count" in text
    assert "2" in text
    assert "10" in text


def test_format_text_undeclared_warning():
    result = _result(rows_checked=3, undeclared=["email", "internal_ref"])
    text = format_text(result)
    assert "email" in text
    assert "internal_ref" in text
    assert "⚠" in text


def test_format_text_long_lines_truncated():
    long_error = "x" * 200
    result = _result(
        rows_checked=1,
        failures=[RowFailure(row_number=1, column="col", error=long_error)],
    )
    for line in format_text(result).splitlines():
        assert len(line) <= 80, f"line too long ({len(line)}): {line!r}"


def test_format_json_fields():
    result = _result(
        rows_checked=5,
        failures=[RowFailure(row_number=2, column="amount", error="bad value")],
        undeclared=["extra"],
    )
    d = format_json(result)
    assert d["rows_checked"] == 5
    assert d["undeclared_columns"] == ["extra"]
    assert len(d["failures"]) == 1
    assert d["failures"][0] == {"row": 2, "column": "amount", "error": "bad value"}


def test_format_json_round_trips():
    result = _result(
        rows_checked=3,
        failures=[RowFailure(row_number=1, column="x", error="oops")],
        undeclared=["y"],
    )
    assert json.loads(json.dumps(format_json(result))) == format_json(result)


def test_format_text_clean():
    result = _result(rows_checked=5)
    text = format_text(result)
    assert "5" in text
    assert "no failures" in text.lower()
