import io
import json

import pytest

from filedge.parser import CSVParser, NDJSONParser, get_parser


def test_csv_parser_yields_dicts():
    f = io.StringIO("name,age\nAlice,30\nBob,25\n")
    rows = list(CSVParser().parse(f))
    assert rows == [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]


def test_csv_parser_empty_body():
    f = io.StringIO("name,age\n")
    assert list(CSVParser().parse(f)) == []


def test_csv_parser_strips_nothing_extra():
    f = io.StringIO("id,value\n1,hello world\n")
    rows = list(CSVParser().parse(f))
    assert rows[0]["value"] == "hello world"


def test_get_parser_csv():
    assert isinstance(get_parser("csv"), CSVParser)


def test_get_parser_unknown_raises():
    with pytest.raises(ValueError, match="Unknown format"):
        get_parser("avro")


# --- NDJSONParser ---

def test_ndjson_parser_yields_dicts():
    f = io.StringIO('{"name": "Alice", "age": 30}\n{"name": "Bob", "age": 25}\n')
    rows = list(NDJSONParser().parse(f))
    assert rows == [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]


def test_ndjson_parser_skips_blank_lines():
    f = io.StringIO('{"name": "Alice"}\n\n\n{"name": "Bob"}\n')
    rows = list(NDJSONParser().parse(f))
    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"


def test_ndjson_parser_raises_on_invalid_json():
    f = io.StringIO('{"name": "Alice"}\nnot valid json\n')
    with pytest.raises(json.JSONDecodeError):
        list(NDJSONParser().parse(f))


def test_get_parser_ndjson():
    assert isinstance(get_parser("ndjson"), NDJSONParser)
