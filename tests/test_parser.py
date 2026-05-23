import json

import pytest

from etl.parser import CSVParser, NDJSONParser, get_parser


def test_csv_parser_yields_dicts(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25\n")

    with open(f, newline="", encoding="utf-8") as fh:
        rows = list(CSVParser().parse(fh))
    assert rows == [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]


def test_csv_parser_empty_body(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("name,age\n")

    with open(f, newline="", encoding="utf-8") as fh:
        assert list(CSVParser().parse(fh)) == []


def test_csv_parser_strips_nothing_extra(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("id,value\n1,hello world\n")

    with open(f, newline="", encoding="utf-8") as fh:
        rows = list(CSVParser().parse(fh))
    assert rows[0]["value"] == "hello world"


def test_get_parser_csv():
    assert isinstance(get_parser("csv"), CSVParser)


def test_get_parser_unknown_raises():
    with pytest.raises(ValueError, match="Unknown format"):
        get_parser("parquet")


# --- NDJSONParser ---

def test_ndjson_parser_yields_dicts(tmp_path):
    f = tmp_path / "data.ndjson"
    f.write_text('{"name": "Alice", "age": 30}\n{"name": "Bob", "age": 25}\n')

    with open(f, encoding="utf-8") as fh:
        rows = list(NDJSONParser().parse(fh))
    assert rows == [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]


def test_ndjson_parser_skips_blank_lines(tmp_path):
    f = tmp_path / "data.ndjson"
    f.write_text('{"name": "Alice"}\n\n\n{"name": "Bob"}\n')

    with open(f, encoding="utf-8") as fh:
        rows = list(NDJSONParser().parse(fh))
    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"


def test_ndjson_parser_raises_on_invalid_json(tmp_path):
    f = tmp_path / "data.ndjson"
    f.write_text('{"name": "Alice"}\nnot valid json\n')

    with pytest.raises(json.JSONDecodeError):
        with open(f, encoding="utf-8") as fh:
            list(NDJSONParser().parse(fh))


def test_get_parser_ndjson():
    assert isinstance(get_parser("ndjson"), NDJSONParser)
