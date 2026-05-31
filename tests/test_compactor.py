import gzip
import json
import os

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.compactor import compact


def _data_files(output_path):
    """Return batch files in output dir, excluding the .filedge metadata directory."""
    return [f for f in output_path.iterdir() if f.name != ".filedge" and not f.name.endswith(".tmp")]


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


def test_compact_creates_missing_output_dir(source, tmp_path):
    # The output directory need not exist beforehand — compact creates it, like
    # the guide's `--output ./compacted` implies (no mkdir step).
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    missing = tmp_path / "does-not-exist-yet"
    assert not missing.exists()

    result = compact(str(source), str(missing))

    assert result["files_compacted"] == 1
    assert missing.is_dir()
    assert len(_data_files(missing)) == 1


def test_compact_merges_files(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}, {"id": 2}])
    _write_ndjson(source / "b.ndjson", [{"id": 3}])

    result = compact(str(source), str(output))

    assert result["batches"] == 1
    assert result["files_compacted"] == 2

    out_files = _data_files(output)
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
    out_files = sorted(_data_files(output))
    assert len(out_files) == 3


def test_compact_compress_produces_gz(source, output):
    _write_ndjson(source / "a.ndjson", [{"x": 1}, {"x": 2}])

    compact(str(source), str(output), compress=True)

    out_files = _data_files(output)
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

    names = sorted(f.name for f in _data_files(output))
    assert "_0001.ndjson" in names[0]
    assert "_0002.ndjson" in names[1]


def test_compact_skips_blank_lines(source, output):
    (source / "a.ndjson").write_text('{"id": 1}\n\n{"id": 2}\n\n')

    compact(str(source), str(output))

    rows = _read_ndjson(_data_files(output)[0])
    assert len(rows) == 2


def test_compact_normalises_json_whitespace(source, output):
    (source / "a.ndjson").write_text('{"b": 2,   "a":  1}\n')

    compact(str(source), str(output))

    rows = _read_ndjson(_data_files(output)[0])
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


def test_compact_manifest_mode_skips_already_processed(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    _write_ndjson(source / "b.ndjson", [{"id": 2}])

    result1 = compact(str(source), str(output))
    assert result1["files_compacted"] == 2

    # New file arrives
    _write_ndjson(source / "c.ndjson", [{"id": 3}])

    result2 = compact(str(source), str(output))
    assert result2["files_compacted"] == 1  # only c.ndjson
    assert result2["batches"] == 1

    # Two batch files total; all three rows present across them
    batch_files = sorted(_data_files(output))
    assert len(batch_files) == 2
    all_rows = []
    for bf in batch_files:
        all_rows.extend(_read_ndjson(bf))
    assert {r["id"] for r in all_rows} == {1, 2, 3}


def test_compact_manifest_mode_rerun_with_no_new_files(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    compact(str(source), str(output))

    result = compact(str(source), str(output))
    assert result == {"batches": 0, "files_compacted": 0}


def test_compact_manifest_not_visible_to_list_files(source, output):
    from filedge.filesystem import list_files
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    compact(str(source), str(output))

    # list_files on the output dir must not see the manifest (check filenames only)
    listed = list_files(None, str(output))
    basenames = [os.path.basename(p) for p in listed]
    assert all(not b.startswith(".filedge") for b in basenames)
    assert all("compact_manifest" not in b for b in basenames)


def test_compact_delete_source_removes_source_files(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    _write_ndjson(source / "b.ndjson", [{"id": 2}])

    result = compact(str(source), str(output), delete_source=True)

    assert result["files_compacted"] == 2
    assert list(source.iterdir()) == []  # source files deleted


def test_compact_delete_source_rerun_is_noop(source, output):
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    compact(str(source), str(output), delete_source=True)

    result = compact(str(source), str(output), delete_source=True)
    assert result == {"batches": 0, "files_compacted": 0}


def test_list_files_excludes_tmp(source):
    from filedge.filesystem import list_files
    _write_ndjson(source / "a.ndjson", [{"id": 1}])
    (source / "partial.ndjson.tmp").write_text("incomplete")

    files = list_files(None, str(source))
    assert all(not f.endswith(".tmp") for f in files)
    assert len(files) == 1


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
