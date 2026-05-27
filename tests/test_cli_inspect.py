import os

import yaml
from click.testing import CliRunner

from filedge.cli import cli

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _inspect(*args, tmp_path=None, output_file=None):
    """Invoke etl inspect, writing YAML to a temp file so we can parse it cleanly."""
    runner = CliRunner()
    cmd = ["inspect"] + list(args)
    if output_file:
        cmd += ["--output", str(output_file)]
    return runner.invoke(cli, cmd)


def test_inspect_csv_exits_zero(tmp_path):
    out = tmp_path / "cols.yaml"
    result = _inspect(os.path.join(FIXTURES, "sample.csv"), output_file=out)
    assert result.exit_code == 0


def test_inspect_csv_produces_valid_yaml(tmp_path):
    out = tmp_path / "cols.yaml"
    _inspect(os.path.join(FIXTURES, "sample.csv"), output_file=out)
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed
    assert len(parsed["columns"]) > 0


def test_inspect_ndjson_exits_zero(tmp_path):
    out = tmp_path / "cols.yaml"
    result = _inspect(os.path.join(FIXTURES, "sample.ndjson"), output_file=out)
    assert result.exit_code == 0
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed


def test_inspect_unknown_extension_without_format_flag_exits_nonzero():
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", "data.xyz"])
    assert result.exit_code != 0
    assert "--format" in result.output or "--format" in (result.output or "")


def test_inspect_format_flag_overrides_extension(tmp_path):
    import shutil
    dst = tmp_path / "data.dat"
    shutil.copy(os.path.join(FIXTURES, "sample.csv"), dst)
    out = tmp_path / "cols.yaml"
    result = _inspect(str(dst), "--format", "csv", output_file=out)
    assert result.exit_code == 0
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed


def test_inspect_sample_rows_comment_in_yaml(tmp_path):
    out = tmp_path / "cols.yaml"
    _inspect(os.path.join(FIXTURES, "sample.csv"), "--sample-rows", "2", output_file=out)
    content = out.read_text()
    assert "2" in content  # sample_rows appears in comment block


def test_inspect_output_flag_writes_yaml_to_file(tmp_path):
    out = tmp_path / "columns.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, [
        "inspect", os.path.join(FIXTURES, "sample.csv"),
        "--output", str(out),
    ])
    assert result.exit_code == 0
    assert out.exists()
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed
    # YAML went to file — stdout contains only the summary (no columns: block)
    assert "columns:" not in result.output


def test_inspect_stdout_yaml_when_no_output_flag():
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", os.path.join(FIXTURES, "sample.csv")])
    assert result.exit_code == 0
    assert "columns:" in result.output


def test_inspect_iso_date_output_uses_executable_date_type(tmp_path):
    source = tmp_path / "dates.csv"
    source.write_text("created\n2024-01-15\n2024-06-30\n")
    out = tmp_path / "cols.yaml"

    result = _inspect(str(source), output_file=out)

    assert result.exit_code == 0
    parsed = yaml.safe_load(out.read_text())
    assert parsed["columns"][0]["type"] == "date"


def test_inspect_non_iso_date_like_output_stays_string(tmp_path):
    source = tmp_path / "dates.csv"
    source.write_text("created\n01/15/2024\n06/30/2024\n")
    out = tmp_path / "cols.yaml"

    result = _inspect(str(source), output_file=out)

    assert result.exit_code == 0
    parsed = yaml.safe_load(out.read_text())
    assert parsed["columns"][0]["type"] == "string"
    assert "filedge date requires YYYY-MM-DD" in result.output


def _build_xlsx(path, *sheets):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))


def test_inspect_xlsx_exits_zero(tmp_path):
    import pytest as _pytest
    _pytest.importorskip("openpyxl")
    src = tmp_path / "data.xlsx"
    _build_xlsx(src, ("Sheet1", [["a", "b"], ["1", "x"], ["2", "y"]]))
    out = tmp_path / "cols.yaml"
    result = _inspect(str(src), output_file=out)
    assert result.exit_code == 0
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed
    assert [c["source"] for c in parsed["columns"]] == ["a", "b"]


def test_inspect_xlsx_sheet_flag_selects_by_name(tmp_path):
    import pytest as _pytest
    _pytest.importorskip("openpyxl")
    src = tmp_path / "wb.xlsx"
    _build_xlsx(
        src,
        ("Customers", [["wrong"], ["x"]]),
        ("Orders", [["order_id", "amount"], ["A-1", "9.99"]]),
    )
    out = tmp_path / "cols.yaml"
    result = _inspect(str(src), "--sheet", "Orders", output_file=out)
    assert result.exit_code == 0
    parsed = yaml.safe_load(out.read_text())
    assert [c["source"] for c in parsed["columns"]] == ["order_id", "amount"]


def test_inspect_xlsx_header_note_includes_sheet(tmp_path):
    import pytest as _pytest
    _pytest.importorskip("openpyxl")
    src = tmp_path / "wb.xlsx"
    _build_xlsx(
        src,
        ("Customers", [["wrong"], ["x"]]),
        ("Orders", [["order_id", "amount"], ["A-1", "9.99"]]),
    )
    out = tmp_path / "cols.yaml"
    _inspect(str(src), "--sheet", "Orders", output_file=out)
    text = out.read_text()
    assert "Orders" in text
    assert "sheet" in text.lower()


def test_inspect_xlsx_header_note_records_default_sheet(tmp_path):
    # When --sheet is omitted, the YAML still records which sheet was read so
    # the inferred config is reproducible.
    import pytest as _pytest
    _pytest.importorskip("openpyxl")
    src = tmp_path / "wb.xlsx"
    _build_xlsx(
        src,
        ("Orders", [["order_id"], ["A-1"]]),
        ("Customers", [["cid"], ["C-1"]]),
    )
    out = tmp_path / "cols.yaml"
    _inspect(str(src), output_file=out)
    text = out.read_text()
    assert "Orders" in text  # first sheet name recorded
    assert "sheet" in text.lower()


def test_inspect_fixed_width_hard_errors_with_docs_pointer(tmp_path):
    source = tmp_path / "transactions.fwf"
    source.write_text("ACME000123\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", str(source), "--format", "fixed_width"])
    assert result.exit_code != 0
    assert "fixed_width" in result.output or "fixed-width" in result.output
    assert "docs/guides/fixed-width.md" in result.output
