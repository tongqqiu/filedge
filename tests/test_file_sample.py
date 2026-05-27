import json

import pytest

from filedge.file_sample import (
    FormatNotDetected,
    open_sample,
    read_parquet_schema,
    resolve_format,
)


def test_resolve_format_uses_explicit_value_when_provided():
    assert resolve_format("unknown.bin", "csv") == "csv"


def test_resolve_format_detects_csv_from_extension():
    assert resolve_format("rows.csv") == "csv"


def test_resolve_format_maps_jsonl_and_ndjson_to_ndjson():
    assert resolve_format("a.jsonl") == "ndjson"
    assert resolve_format("a.ndjson") == "ndjson"


def test_resolve_format_returns_typed_failure_for_unknown_extension():
    result = resolve_format("rows.txt")
    assert isinstance(result, FormatNotDetected)
    assert result.file == "rows.txt"
    assert result.extension == ".txt"


def test_resolve_format_returns_typed_failure_for_no_extension():
    result = resolve_format("rows")
    assert isinstance(result, FormatNotDetected)
    assert result.extension == ""


def test_open_sample_yields_full_csv_stream_by_default(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("name,value\nAda,1\nBea,2\nCarl,3\n")

    with open_sample(str(path), "csv") as rows:
        materialized = list(rows)

    assert [row["name"] for row in materialized] == ["Ada", "Bea", "Carl"]


def test_open_sample_applies_start_row_and_num_rows_window(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("name,value\nAda,1\nBea,2\nCarl,3\nDan,4\n")

    with open_sample(str(path), "csv", start_row=2, num_rows=2) as rows:
        materialized = list(rows)

    assert [row["name"] for row in materialized] == ["Bea", "Carl"]


def test_open_sample_reads_ndjson(tmp_path):
    path = tmp_path / "rows.ndjson"
    path.write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n")

    with open_sample(str(path), "ndjson") as rows:
        materialized = list(rows)

    assert materialized == [{"a": 1}, {"a": 2}]


def test_open_sample_propagates_open_errors(tmp_path):
    missing = tmp_path / "nope.csv"
    with pytest.raises(FileNotFoundError):
        with open_sample(str(missing), "csv") as rows:
            list(rows)


def test_open_sample_uses_provided_encoding(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_bytes("name\nAda\n".encode("utf-16"))

    with open_sample(str(path), "csv", encoding="utf-16") as rows:
        materialized = list(rows)

    assert materialized == [{"name": "Ada"}]


def test_open_sample_reads_fixed_width_with_layout(tmp_path):
    from filedge.fixed_width import LayoutColumn

    path = tmp_path / "rows.fwf"
    path.write_text("ACME000123\nFOOO000456\n")

    layout = [
        LayoutColumn(name="account", start=1, width=4),
        LayoutColumn(name="number", start=5, width=6),
    ]
    with open_sample(str(path), "fixed_width", columns=layout) as rows:
        materialized = list(rows)

    assert materialized == [
        {"account": "ACME", "number": "000123"},
        {"account": "FOOO", "number": "000456"},
    ]


def test_resolve_format_detects_xlsx_from_extension():
    assert resolve_format("data.xlsx") == "excel"


def test_resolve_format_xls_is_not_supported():
    # .xls is deliberately not mapped (ADR-0012). Users should re-save as .xlsx.
    result = resolve_format("data.xls")
    assert isinstance(result, FormatNotDetected)
    assert result.extension == ".xls"


def test_supported_formats_includes_excel():
    from filedge.file_sample import SUPPORTED_FORMATS

    assert "excel" in SUPPORTED_FORMATS


def test_open_sample_threads_sheet_to_excel_parser(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")

    path = tmp_path / "wb.xlsx"
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    a = wb.create_sheet("First")
    a.append(["x"])
    a.append(["from-first"])
    b = wb.create_sheet("Second")
    b.append(["x"])
    b.append(["from-second"])
    wb.save(str(path))

    from filedge.file_sample import open_sample

    with open_sample(str(path), "excel", sheet="Second") as rows:
        materialized = list(rows)

    assert materialized == [{"x": "from-second"}]


def test_read_parquet_schema_returns_arrow_schema(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")

    path = tmp_path / "rows.parquet"
    table = pa.table({"name": ["Ada", "Bea"], "value": [1, 2]})
    pq.write_table(table, str(path))

    schema = read_parquet_schema(str(path))
    assert [schema.field(i).name for i in range(len(schema))] == ["name", "value"]
