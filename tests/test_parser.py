import io
import json

import pytest

from filedge.parser import CSVParser, NDJSONParser, get_parser, parser_kwargs_for


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


# --- fixed_width via factory ---

def test_get_parser_fixed_width_builds_parser_from_columns():
    from filedge.fixed_width import FixedWidthParser, LayoutColumn
    parser = get_parser(
        "fixed_width",
        columns=[LayoutColumn(name="a", start=1, width=4)],
    )
    assert isinstance(parser, FixedWidthParser)


def test_get_parser_fixed_width_requires_columns():
    with pytest.raises(ValueError, match="fixed_width.*columns"):
        get_parser("fixed_width")


# --- excel via factory ---


def test_get_parser_excel_builds_parser_with_sheet():
    pytest.importorskip("openpyxl")
    from filedge.excel import ExcelParser

    parser = get_parser("excel", sheet="Orders")
    assert isinstance(parser, ExcelParser)
    assert parser._sheet == "Orders"


def test_get_parser_excel_defaults_sheet_to_none():
    pytest.importorskip("openpyxl")
    from filedge.excel import ExcelParser

    parser = get_parser("excel")
    assert isinstance(parser, ExcelParser)
    assert parser._sheet is None


# --- parser_kwargs_for: the Pipeline Config -> Parser binding ---


def _column(source, *, start=None, width=None):
    from filedge.config import ColumnMapping

    return ColumnMapping(
        source=source, dest=source, type="string", start=start, width=width
    )


def test_parser_kwargs_for_stateless_format_is_empty():
    assert parser_kwargs_for("csv", columns=[_column("id")]) == {}
    assert parser_kwargs_for("ndjson") == {}


def test_parser_kwargs_for_fixed_width_builds_layout():
    from filedge.fixed_width import LayoutColumn

    kwargs = parser_kwargs_for(
        "fixed_width", columns=[_column("a", start=1, width=4)]
    )
    assert kwargs == {"columns": [LayoutColumn(name="a", start=1, width=4)]}


def test_parser_kwargs_for_fixed_width_without_columns_raises():
    with pytest.raises(ValueError, match="fixed_width requires"):
        parser_kwargs_for("fixed_width")


def test_parser_kwargs_for_excel_passes_sheet_through():
    assert parser_kwargs_for("excel", sheet="Orders") == {"sheet": "Orders"}
    assert parser_kwargs_for("excel") == {"sheet": None}
