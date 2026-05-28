"""Direct tests of the Authoring Session — the deep module the CLI and the
future Authoring UI both drive. These exercise the interface without Click, the
payoff of pulling orchestration out of cli.py."""

import os

import pytest

from filedge.authoring import AuthoringSession
from filedge.config import load_config

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _build_xlsx(path, *sheets):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))


def test_infer_schema_csv():
    session = AuthoringSession(os.path.join(FIXTURES, "sample.csv"), "csv")
    columns = session.infer_schema(sample_rows=1000)
    names = [c.name for c in columns]
    assert "id" in names


def test_preview_returns_row_dicts(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("id,val\n1,a\n2,b\n3,c\n")
    session = AuthoringSession(str(csv), "csv")
    rows = session.preview(start_row=2, num_rows=1)
    assert rows == [{"id": "2", "val": "b"}]


def test_validate_requires_config(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("id\n1\n")
    session = AuthoringSession(str(csv), "csv")
    with pytest.raises(ValueError, match="Pipeline Config"):
        session.validate()


def test_validate_against_config(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: csv\ndest_table: t\n"
        "columns:\n  - source: id\n    dest: id\n    type: integer\n    required: true\n"
    )
    src = tmp_path / "data.csv"
    src.write_text("id\n1\n2\nx\n")
    session = AuthoringSession(str(src), "csv", config=load_config(str(cfg)))
    result = session.validate()
    assert result.rows_checked == 3
    assert any(f.column == "id" for f in result.failures)


def test_sheet_name_is_none_for_non_excel():
    session = AuthoringSession(os.path.join(FIXTURES, "sample.csv"), "csv")
    assert session.sheet_name is None


def test_fixed_width_without_config_raises(tmp_path):
    src = tmp_path / "t.fwf"
    src.write_text("ACME01\n")
    session = AuthoringSession(str(src), "fixed_width")
    with pytest.raises(ValueError, match="fixed_width requires"):
        session.preview()


def test_excel_sheet_name_resolves_default(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "wb.xlsx"
    _build_xlsx(
        src,
        ("Orders", [["order_id"], ["A-1"]]),
        ("Customers", [["cid"], ["C-1"]]),
    )
    session = AuthoringSession(str(src), "excel")
    assert session.sheet_name == "Orders"


def test_excel_sheet_selector_by_index(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "wb.xlsx"
    _build_xlsx(
        src,
        ("First", [["a"], ["from-first"]]),
        ("Second", [["a"], ["from-second"]]),
    )
    session = AuthoringSession(str(src), "excel", sheet=1)
    assert session.sheet_name == "Second"
    assert session.preview() == [{"a": "from-second"}]


def test_excel_sheet_falls_back_to_config(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "orders.xlsx"
    _build_xlsx(
        src,
        ("Orders", [["order_id"], ["A-1"]]),
        ("Overflow", [["order_id"], ["B-1"]]),
    )
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: excel\ndest_table: t\nexcel:\n  sheet: Overflow\n"
        "columns:\n  - source: order_id\n    dest: order_id\n    type: string\n"
    )
    session = AuthoringSession(str(src), "excel", config=load_config(str(cfg)))
    assert session.sheet_name == "Overflow"
    assert session.preview() == [{"order_id": "B-1"}]


def test_explicit_sheet_overrides_config(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "orders.xlsx"
    _build_xlsx(
        src,
        ("Orders", [["order_id"], ["A-1"]]),
        ("Overflow", [["order_id"], ["B-1"]]),
    )
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: excel\ndest_table: t\nexcel:\n  sheet: Overflow\n"
        "columns:\n  - source: order_id\n    dest: order_id\n    type: string\n"
    )
    session = AuthoringSession(
        str(src), "excel", config=load_config(str(cfg)), sheet="Orders"
    )
    assert session.sheet_name == "Orders"
    assert session.preview() == [{"order_id": "A-1"}]


def test_encoding_falls_back_to_config(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: csv\ndest_table: t\nencoding: utf-8\n"
        "columns:\n  - source: id\n    dest: id\n    type: integer\n"
    )
    src = tmp_path / "data.csv"
    src.write_text("id\n1\n")
    session = AuthoringSession(str(src), "csv", config=load_config(str(cfg)))
    assert session._encoding == "utf-8"
