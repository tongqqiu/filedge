"""Tests for the Excel (.xlsx) Parser. See ADR-0012."""

import datetime

import pytest

openpyxl = pytest.importorskip("openpyxl")

from filedge.excel import ExcelParser  # noqa: E402  (must follow importorskip)


def _write_xlsx(path, *sheets):
    """Build a .xlsx fixture. `sheets` is an iterable of (name, rows) pairs.

    `rows` is a list of lists; cells are written verbatim so callers control the
    Python type that lands in each cell (int, float, bool, datetime, None, str).
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))


# --- Tracer bullet ---------------------------------------------------------


def test_excel_parser_yields_dicts_with_row_1_as_header(tmp_path):
    path = tmp_path / "orders.xlsx"
    _write_xlsx(
        path,
        ("Sheet1", [["name", "amount"], ["Alice", "10"], ["Bob", "20"]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [
        {"name": "Alice", "amount": "10"},
        {"name": "Bob", "amount": "20"},
    ]


# --- Sheet selection -------------------------------------------------------


def test_excel_parser_selects_sheet_by_name(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Orders", [["a"], ["from-orders"]]),
        ("Customers", [["a"], ["from-customers"]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser(sheet="Customers").parse(f))

    assert rows == [{"a": "from-customers"}]


def test_excel_parser_selects_sheet_by_zero_indexed_int(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("First", [["a"], ["from-first"]]),
        ("Second", [["a"], ["from-second"]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser(sheet=1).parse(f))

    assert rows == [{"a": "from-second"}]


def test_excel_parser_raises_on_missing_sheet_name(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(path, ("Orders", [["a"], ["x"]]))

    with open(path, "rb") as f:
        with pytest.raises(ValueError, match="Missing"):
            list(ExcelParser(sheet="Nope").parse(f))


def test_excel_parser_raises_on_out_of_range_sheet_index(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(path, ("Orders", [["a"], ["x"]]))

    with open(path, "rb") as f:
        with pytest.raises(ValueError, match="out of range"):
            list(ExcelParser(sheet=3).parse(f))


def test_excel_parser_single_sheet_default_does_not_warn(tmp_path, capsys):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(path, ("Orders", [["a"], ["x"]]))

    with open(path, "rb") as f:
        list(ExcelParser().parse(f))

    captured = capsys.readouterr()
    assert "sheet" not in captured.err.lower()


def test_excel_parser_multi_sheet_default_warns_to_stderr_and_picks_first(
    tmp_path, capsys
):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Orders", [["a"], ["from-orders"]]),
        ("Customers", [["a"], ["from-customers"]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [{"a": "from-orders"}]
    captured = capsys.readouterr()
    assert "Orders" in captured.err
    assert "Customers" in captured.err


# --- Cell coercion ---------------------------------------------------------


def test_excel_parser_coerces_datetime_to_isoformat(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Sheet1", [["t"], [datetime.datetime(2024, 1, 15, 9, 30, 5)]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [{"t": "2024-01-15T09:30:05"}]


def test_excel_parser_coerces_date_to_isoformat(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Sheet1", [["d"], [datetime.date(2024, 1, 15)]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    # openpyxl reads bare date cells as datetime at midnight; the parser must
    # still produce an ISO string. We assert the date prefix is preserved.
    assert rows[0]["d"].startswith("2024-01-15")


def test_excel_parser_coerces_bool_before_int(tmp_path):
    # Python: isinstance(True, int) is True. The parser must check bool first
    # so True does not become "1".
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Sheet1", [["flag"], [True], [False]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [{"flag": "True"}, {"flag": "False"}]


def test_excel_parser_coerces_int_and_float_to_str(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Sheet1", [["i", "f"], [42, 3.14]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [{"i": "42", "f": "3.14"}]


def test_excel_parser_empty_cells_yield_none(tmp_path):
    path = tmp_path / "wb.xlsx"
    _write_xlsx(
        path,
        ("Sheet1", [["a", "b"], ["x", None]]),
    )

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [{"a": "x", "b": None}]


def test_excel_parser_formula_with_data_only_reads_cached_value(tmp_path):
    # When openpyxl is opened with data_only=True, formula cells should yield
    # the cached computed value (what Excel last evaluated and saved). We
    # round-trip through the ZIP container to inject a <v>3</v> next to the
    # formula <f>1+2</f> — the same shape Excel writes on save.
    import shutil
    import zipfile

    path = tmp_path / "wb.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "result"
    ws["A2"] = "=1+2"
    wb.save(str(path))

    sheet_path = "xl/worksheets/sheet1.xml"
    tmp_zip = tmp_path / "tmp.xlsx"
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(
        tmp_zip, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == sheet_path:
                xml = data.decode("utf-8").replace(
                    "<f>1+2</f>",
                    '<f>1+2</f><v>3</v>',
                )
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(str(tmp_zip), str(path))

    with open(path, "rb") as f:
        rows = list(ExcelParser().parse(f))

    assert rows == [{"result": "3"}]


# --- Lazy ImportError ------------------------------------------------------


def test_excel_parser_raises_helpful_import_error_when_openpyxl_missing(
    tmp_path, monkeypatch
):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("no module named openpyxl")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="uv sync --extra excel"):
        list(ExcelParser().parse(open(tmp_path / "missing.xlsx", "wb")))
