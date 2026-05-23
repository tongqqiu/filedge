import os

from click.testing import CliRunner

from filedge.cli import cli

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _preview(*args):
    runner = CliRunner()
    return runner.invoke(cli, ["preview"] + list(args))


def test_preview_csv_exits_zero():
    result = _preview(os.path.join(FIXTURES, "sample.csv"))
    assert result.exit_code == 0


def test_preview_output_contains_headers():
    result = _preview(os.path.join(FIXTURES, "sample.csv"))
    assert "id" in result.output
    assert "name" in result.output
    assert "amount" in result.output


def test_preview_output_contains_values():
    result = _preview(os.path.join(FIXTURES, "sample.csv"))
    assert "Alice" in result.output


def test_preview_default_rows_does_not_exceed_ten(tmp_path):
    # Write a CSV with 20 rows
    csv = tmp_path / "big.csv"
    csv.write_text("id,val\n" + "".join(f"{i},row{i}\n" for i in range(1, 21)))
    result = _preview(str(csv))
    assert result.exit_code == 0
    # Rows in output: header + separator + data rows; data rows <= 10
    data_lines = [
        line for line in result.output.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]
    assert len(data_lines) <= 10


def test_rows_flag_limits_output(tmp_path):
    csv = tmp_path / "big.csv"
    csv.write_text("id,val\n" + "".join(f"{i},row{i}\n" for i in range(1, 21)))
    result = _preview(str(csv), "--rows", "3")
    assert result.exit_code == 0
    assert "row3" in result.output
    assert "row4" not in result.output


def test_unknown_extension_exits_two():
    result = _preview("data.xyz")
    assert result.exit_code == 2


def test_preview_shows_row_numbers():
    result = _preview(os.path.join(FIXTURES, "sample.csv"))
    assert "1" in result.output
    assert "2" in result.output


def test_start_row_skips_earlier_rows(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("id,val\n" + "".join(f"{i},row{i}\n" for i in range(1, 11)))
    result = _preview(str(csv), "--start-row", "5")
    assert result.exit_code == 0
    assert "| row5 " in result.output or "row5\n" in result.output
    assert "| row4 " not in result.output
    assert "| row1 " not in result.output


def test_start_row_shows_file_row_numbers(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("id,val\n" + "".join(f"{i},row{i}\n" for i in range(1, 11)))
    result = _preview(str(csv), "--start-row", "8", "--rows", "2")
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
    assert lines[0].strip().startswith("8")
    assert lines[1].strip().startswith("9")


def test_start_row_beyond_file_shows_no_rows(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("id,val\n1,a\n2,b\n")
    result = _preview(str(csv), "--start-row", "99")
    assert result.exit_code == 0
    assert "(no rows)" in result.output


def test_cloud_path_preview(tmp_path):
    pytest_skip = False
    try:
        import fsspec  # noqa: F401
    except ImportError:
        pytest_skip = True
    if pytest_skip:
        return
    import fsspec
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    with fs.open("/bucket/data.csv", "w") as f:
        f.write("id,name\n1,Alice\n2,Bob\n")
    result = _preview("memory:///bucket/data.csv")
    assert result.exit_code == 0
    assert "Alice" in result.output
    fs.store.clear()
