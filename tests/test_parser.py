import pytest

from etl.parser import CSVParser, get_parser


def test_csv_parser_yields_dicts(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25\n")

    rows = list(CSVParser().parse(str(f)))
    assert rows == [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]


def test_csv_parser_empty_body(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("name,age\n")

    assert list(CSVParser().parse(str(f))) == []


def test_csv_parser_strips_nothing_extra(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("id,value\n1,hello world\n")

    rows = list(CSVParser().parse(str(f)))
    assert rows[0]["value"] == "hello world"


def test_get_parser_csv():
    assert isinstance(get_parser("csv"), CSVParser)


def test_get_parser_unknown_raises():
    with pytest.raises(ValueError, match="Unknown format"):
        get_parser("parquet")
