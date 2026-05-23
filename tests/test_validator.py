from etl.config import ColumnMapping
from etl.validator import validate_file


def _col(source, type="string", required=False):
    return ColumnMapping(source=source, dest=source, type=type, required=required)


def test_bad_value_produces_row_failure():
    rows = [{"amount": "not-a-number"}]
    columns = [_col("amount", "float")]
    result = validate_file(iter(rows), columns)
    assert result.rows_checked == 1
    assert len(result.failures) == 1
    f = result.failures[0]
    assert f.row_number == 1
    assert f.column == "amount"
    assert "not-a-number" in f.error


def test_rows_checked_includes_clean_and_bad_rows():
    rows = [
        {"amount": "10.0"},
        {"amount": "bad"},
        {"amount": "20.0"},
    ]
    columns = [_col("amount", "float")]
    result = validate_file(iter(rows), columns)
    assert result.rows_checked == 3
    assert len(result.failures) == 1
    assert result.failures[0].row_number == 2


def test_multiple_bad_columns_same_row_each_get_failure():
    rows = [{"amount": "bad", "count": "also-bad"}]
    columns = [_col("amount", "float"), _col("count", "integer")]
    result = validate_file(iter(rows), columns)
    assert result.rows_checked == 1
    assert len(result.failures) == 2
    assert {f.column for f in result.failures} == {"amount", "count"}
    assert all(f.row_number == 1 for f in result.failures)


def test_missing_required_column_is_failure():
    rows = [{"name": "Alice"}]
    columns = [_col("name"), _col("amount", "float", required=True)]
    result = validate_file(iter(rows), columns)
    assert len(result.failures) == 1
    assert result.failures[0].column == "amount"


def test_required_column_empty_is_failure():
    rows = [{"amount": ""}]
    columns = [_col("amount", "float", required=True)]
    result = validate_file(iter(rows), columns)
    assert len(result.failures) == 1
    assert result.failures[0].column == "amount"


def test_undeclared_columns_detected():
    rows = [{"name": "Alice", "internal_ref": "x", "email": "a@b.com"}]
    columns = [_col("name")]
    result = validate_file(iter(rows), columns)
    assert set(result.undeclared_columns) == {"internal_ref", "email"}
    assert result.failures == []


def test_undeclared_columns_not_a_failure():
    rows = [{"name": "Alice", "extra": "ignored"}]
    columns = [_col("name")]
    result = validate_file(iter(rows), columns)
    assert result.failures == []
    assert "extra" in result.undeclared_columns


def test_empty_rows():
    result = validate_file(iter([]), [_col("name")])
    assert result.rows_checked == 0
    assert result.failures == []
    assert result.undeclared_columns == []


def test_all_rows_clean():
    rows = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]
    columns = [_col("name"), _col("age", "integer")]
    result = validate_file(iter(rows), columns)
    assert result.rows_checked == 2
    assert result.failures == []
    assert result.undeclared_columns == []
