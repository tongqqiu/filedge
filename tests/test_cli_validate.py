import json
import os

import pytest
from click.testing import CliRunner

from etl.cli import cli

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

_CLEAN_CONFIG = """\
format: csv
dest_table: t
connector:
  type: sqlite
  url: sqlite:///ignored.db
columns:
  - source: id
    dest: id
    type: integer
    required: true
  - source: amount
    dest: amount
    type: float
    required: false
  - source: name
    dest: name
    type: string
    required: true
"""


def _validate(*args):
    runner = CliRunner()
    return runner.invoke(cli, ["validate"] + list(args))


_STRICT_CONFIG = """\
format: csv
dest_table: t
connector:
  type: sqlite
  url: sqlite:///ignored.db
columns:
  - source: id
    dest: id
    type: integer
    required: true
  - source: amount
    dest: amount
    type: float
    required: true
  - source: name
    dest: name
    type: string
    required: true
"""


def test_valid_csv_exits_zero(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(_CLEAN_CONFIG)
    result = _validate(os.path.join(FIXTURES, "sample.csv"), "--config", str(cfg))
    assert result.exit_code == 0


def test_undeclared_column_is_warning_not_failure(tmp_path):
    # Config only declares 'id' — other columns in sample.csv are undeclared
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: csv\ndest_table: t\n"
        "connector:\n  type: sqlite\n  url: sqlite:///ignored.db\n"
        "columns:\n  - source: id\n    dest: id\n    type: integer\n    required: true\n"
    )
    result = _validate(os.path.join(FIXTURES, "sample.csv"), "--config", str(cfg))
    assert result.exit_code == 0
    assert "⚠" in result.output


def _parse_json_output(output: str) -> dict:
    """Extract the JSON line from output that may also contain text summary lines."""
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"No JSON line found in output: {output!r}")


def test_sample_rows_limits_rows_checked(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(_CLEAN_CONFIG)
    result = _validate(
        os.path.join(FIXTURES, "sample.csv"),
        "--config", str(cfg),
        "--sample-rows", "2",
        "--json",
    )
    assert result.exit_code == 0
    data = _parse_json_output(result.output)
    assert data["rows_checked"] == 2


def test_json_flag_outputs_valid_json(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(_CLEAN_CONFIG)
    result = _validate(os.path.join(FIXTURES, "sample.csv"), "--config", str(cfg), "--json")
    assert result.exit_code == 0
    data = _parse_json_output(result.output)
    assert "rows_checked" in data
    assert "failures" in data
    assert "undeclared_columns" in data


def test_unknown_extension_without_format_exits_two(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(_CLEAN_CONFIG)
    result = _validate("data.xyz", "--config", str(cfg))
    assert result.exit_code == 2


def test_cloud_path_validates_via_fsspec(tmp_path):
    pytest.importorskip("fsspec")
    import fsspec
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    with fs.open("/bucket/data.csv", "w") as f:
        f.write("id,name\n1,Alice\n2,Bob\n")
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: csv\ndest_table: t\n"
        "connector:\n  type: sqlite\n  url: sqlite:///ignored.db\n"
        "columns:\n"
        "  - source: id\n    dest: id\n    type: integer\n    required: true\n"
        "  - source: name\n    dest: name\n    type: string\n    required: true\n"
    )
    result = _validate("memory:///bucket/data.csv", "--config", str(cfg))
    assert result.exit_code == 0
    fs.store.clear()


def test_file_with_failures_exits_one(tmp_path):
    # sample.csv row 3 has empty amount — strict config makes it a failure
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(_STRICT_CONFIG)
    result = _validate(os.path.join(FIXTURES, "sample.csv"), "--config", str(cfg))
    assert result.exit_code == 1
    assert "amount" in result.output
