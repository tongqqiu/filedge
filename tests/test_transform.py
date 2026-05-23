import pytest

from filedge.config import ColumnMapping
from filedge.transform import TransformError, transform_row


def _col(source, dest=None, type="string", required=True):
    return ColumnMapping(source=source, dest=dest or source, type=type, required=required)


# --- Type coercions ---

def test_string_passthrough():
    row = transform_row({"name": "Alice"}, [_col("name")])
    assert row["name"] == "Alice"


def test_integer_coercion():
    row = transform_row({"count": "42"}, [_col("count", type="integer")])
    assert row["count"] == 42
    assert isinstance(row["count"], int)


def test_float_coercion():
    row = transform_row({"amount": "3.14"}, [_col("amount", type="float")])
    assert abs(row["amount"] - 3.14) < 1e-9
    assert isinstance(row["amount"], float)


def test_date_coercion():
    row = transform_row({"day": "2024-01-15"}, [_col("day", type="date")])
    assert row["day"] == "2024-01-15"


def test_timestamp_coercion():
    row = transform_row(
        {"ts": "2024-01-15T10:30:00"}, [_col("ts", type="timestamp")]
    )
    assert row["ts"] == "2024-01-15T10:30:00"


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True),
    ("false", False), ("0", False), ("no", False),
    ("True", True), ("FALSE", False),
])
def test_boolean_coercion(raw, expected):
    row = transform_row({"flag": raw}, [_col("flag", type="boolean")])
    assert row["flag"] is expected


# --- Column name mapping ---

def test_column_name_remapping():
    row = transform_row({"src_id": "7"}, [_col("src_id", dest="id", type="integer")])
    assert "id" in row
    assert "src_id" not in row
    assert row["id"] == 7


# --- Strict mode ---

def test_missing_required_column_raises():
    with pytest.raises(TransformError, match="Missing required column"):
        transform_row({}, [_col("name")])


def test_empty_required_column_raises():
    with pytest.raises(TransformError, match="empty"):
        transform_row({"name": ""}, [_col("name")])


def test_bad_integer_raises():
    with pytest.raises(TransformError, match="Cannot coerce"):
        transform_row({"n": "abc"}, [_col("n", type="integer")])


def test_bad_date_raises():
    with pytest.raises(TransformError, match="Cannot coerce"):
        transform_row({"d": "not-a-date"}, [_col("d", type="date")])


def test_bad_boolean_raises():
    with pytest.raises(TransformError, match="Cannot coerce"):
        transform_row({"b": "maybe"}, [_col("b", type="boolean")])


# --- Column tolerance ---

def test_optional_missing_column_produces_none():
    row = transform_row({}, [_col("note", required=False)])
    assert row["note"] is None


def test_extra_source_columns_silently_ignored():
    row = transform_row(
        {"name": "Alice", "extra": "ignored", "another": "also_ignored"},
        [_col("name")],
    )
    assert row == {"name": "Alice"}
