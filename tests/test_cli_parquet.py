import os

import pytest
from click.testing import CliRunner

from filedge.cli import cli

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _make_parquet_file(tmp_path, data: dict, name="data.parquet") -> str:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / name
    pq.write_table(pa.table(data), str(path))
    return str(path)


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def test_inspect_parquet_exits_zero(tmp_path):
    path = _make_parquet_file(tmp_path, {"id": [1, 2], "name": ["Alice", "Bob"]})
    result = _run("inspect", path)
    assert result.exit_code == 0


def test_inspect_parquet_contains_column_names(tmp_path):
    path = _make_parquet_file(tmp_path, {"id": [1, 2], "amount": [9.99, 5.00]})
    result = _run("inspect", path)
    assert "id" in result.output
    assert "amount" in result.output


def test_inspect_parquet_infers_integer_type(tmp_path):
    path = _make_parquet_file(tmp_path, {"count": [1, 2, 3]})
    result = _run("inspect", path)
    assert "integer" in result.output


def test_inspect_parquet_infers_float_type(tmp_path):
    path = _make_parquet_file(tmp_path, {"amount": [1.5, 2.5]})
    result = _run("inspect", path)
    assert "float" in result.output


def test_preview_parquet_exits_zero(tmp_path):
    path = _make_parquet_file(tmp_path, {"id": [1, 2], "name": ["Alice", "Bob"]})
    result = _run("preview", path)
    assert result.exit_code == 0
    assert "Alice" in result.output


def test_validate_parquet_exits_zero(tmp_path):
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: parquet\ndest_table: t\n"
        "connector:\n  type: sqlite\n  url: sqlite:///ignored.db\n"
        "columns:\n"
        "  - source: id\n    dest: id\n    type: integer\n    required: true\n"
        "  - source: name\n    dest: name\n    type: string\n    required: true\n"
    )
    path = _make_parquet_file(tmp_path, {"id": [1, 2], "name": ["Alice", "Bob"]})
    result = _run("validate", path, "--config", str(cfg))
    assert result.exit_code == 0
