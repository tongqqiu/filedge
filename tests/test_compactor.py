import gzip
import json

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.compactor import compact


def _write_ndjson(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _read_ndjson(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_ndjson_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "landing"
    src.mkdir()
    return src


@pytest.fixture
def output(tmp_path):
    out = tmp_path / "compacted"
    out.mkdir()
    return out


def test_compact_merges_files(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}, {"id": 2}])
    _write_ndjson(source / "b.ndjson", [{"id": 3}])

    result = compact(str(source), str(output))

    assert result["batches"] == 1
    assert result["files_compacted"] == 2

    out_files = list(output.iterdir())
    assert len(out_files) == 1
    rows = _read_ndjson(out_files[0])
    assert len(rows) == 3
    assert {r["id"] for r in rows} == {1, 2, 3}


def test_compact_respects_max_files(source, output):
    for i in range(5):
        _write_ndjson(source / f"{i}.ndjson", [{"id": i}])

    result = compact(str(source), str(output), max_files=2)

    assert result["batches"] == 3  # 2 + 2 + 1
    assert result["files_compacted"] == 5
    out_files = sorted(output.iterdir())
    assert len(out_files) == 3


def test_compact_compress_produces_gz(source, output):
    _write_ndjson(source / "a.ndjson", [{"x": 1}, {"x": 2}])

    compact(str(source), str(output), compress=True)

    out_files = list(output.iterdir())
    assert len(out_files) == 1
    assert out_files[0].name.endswith(".ndjson.gz")
    rows = _read_ndjson_gz(out_files[0])
    assert len(rows) == 2


def test_compact_empty_source_returns_zero(source, output):
    result = compact(str(source), str(output))
    assert result == {"batches": 0, "files_compacted": 0}


def test_compact_output_naming_includes_timestamp_and_index(source, output):
    for i in range(3):
        _write_ndjson(source / f"{i}.ndjson", [{"i": i}])

    compact(str(source), str(output), max_files=2)

    names = sorted(f.name for f in output.iterdir())
    assert names[0].endswith("_0001.ndjson")
    assert names[1].endswith("_0002.ndjson")


def test_compact_skips_blank_lines(source, output):
    (source / "a.ndjson").write_text('{"id": 1}\n\n{"id": 2}\n\n')

    compact(str(source), str(output))

    rows = _read_ndjson(list(output.iterdir())[0])
    assert len(rows) == 2


def test_compact_normalises_json_whitespace(source, output):
    (source / "a.ndjson").write_text('{"b": 2,   "a":  1}\n')

    compact(str(source), str(output))

    rows = _read_ndjson(list(output.iterdir())[0])
    assert rows[0] == {"a": 1, "b": 2}


def test_compact_cli(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _write_ndjson(src / "f.ndjson", [{"v": 1}])

    runner = CliRunner()
    result = runner.invoke(cli, [
        "compact",
        "--watched-dir", str(src),
        "--output", str(out),
    ])

    assert result.exit_code == 0
    assert "Batches written: 1" in result.output
    assert "Files compacted: 1" in result.output


def test_compact_cleans_up_partial_output_on_error(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    (source / "b.ndjson").write_text("not valid json\n")

    with pytest.raises(Exception):
        compact(str(source), str(output))

    assert list(output.iterdir()) == []


def test_compact_cli_compress_flag(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _write_ndjson(src / "f.ndjson", [{"v": 1}])

    runner = CliRunner()
    result = runner.invoke(cli, [
        "compact",
        "--watched-dir", str(src),
        "--output", str(out),
        "--compress",
    ])

    assert result.exit_code == 0
    assert any(f.name.endswith(".ndjson.gz") for f in out.iterdir())
