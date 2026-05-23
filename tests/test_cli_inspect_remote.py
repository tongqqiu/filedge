"""
Remote file support for etl inspect — tested with fsspec's MemoryFileSystem
so no real cloud credentials are required.
"""
import io

import pytest
import yaml

pytest.importorskip("fsspec")

import fsspec
from click.testing import CliRunner

from etl.cli import cli


def _make_memory_csv(path: str, content: str):
    """Write content to the fsspec memory filesystem at the given path."""
    fs = fsspec.filesystem("memory")
    fs.mkdirs(path.rsplit("/", 1)[0], exist_ok=True)
    with fs.open(path, "w") as f:
        f.write(content)


def _make_memory_ndjson(path: str, content: str):
    fs = fsspec.filesystem("memory")
    fs.mkdirs(path.rsplit("/", 1)[0], exist_ok=True)
    with fs.open(path, "w") as f:
        f.write(content)


@pytest.fixture(autouse=True)
def clear_memory_fs():
    """Start each test with a clean in-memory filesystem."""
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    yield
    fs.store.clear()


def test_inspect_remote_csv_via_memory_fs(tmp_path):
    _make_memory_csv(
        "/bucket/data.csv",
        "id,amount\n1,9.99\n2,19.50\n",
    )
    out = tmp_path / "cols.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, [
        "inspect", "memory://bucket/data.csv",
        "--output", str(out),
    ])
    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed
    names = {c["source"] for c in parsed["columns"]}
    assert names == {"id", "amount"}


def test_inspect_remote_ndjson_via_memory_fs(tmp_path):
    _make_memory_ndjson(
        "/bucket/events.ndjson",
        '{"id": 1, "name": "Alice"}\n{"id": 2, "name": "Bob"}\n',
    )
    out = tmp_path / "cols.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, [
        "inspect", "memory://bucket/events.ndjson",
        "--output", str(out),
    ])
    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(out.read_text())
    names = {c["source"] for c in parsed["columns"]}
    assert names == {"id", "name"}


def test_inspect_remote_format_override(tmp_path):
    _make_memory_csv(
        "/bucket/data.dat",
        "x,y\n1,2\n3,4\n",
    )
    out = tmp_path / "cols.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, [
        "inspect", "memory://bucket/data.dat",
        "--format", "csv",
        "--output", str(out),
    ])
    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(out.read_text())
    assert "columns" in parsed


def test_inspect_missing_remote_file_exits_nonzero():
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", "memory://bucket/nonexistent.csv"])
    assert result.exit_code != 0
