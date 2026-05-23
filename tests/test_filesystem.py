import pytest
import fsspec

from etl.filesystem import file_basename, get_filesystem, list_files
from etl.hashing import compute_hash
from etl.loader import load_file
from etl.config import ColumnMapping, PipelineConfig
from etl.connectors.sqlite import SQLiteConnector


# fsspec memory:// filesystem — no cloud credentials required
@pytest.fixture
def memfs():
    return fsspec.filesystem("memory")


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="string", required=True),
        ],
    )


def test_get_filesystem_local(tmp_path):
    fs, root = get_filesystem(str(tmp_path))
    assert fs is None
    assert root == str(tmp_path)


def test_get_filesystem_memory():
    fs, root = get_filesystem("memory://bucket/prefix")
    assert fs is not None
    assert "bucket/prefix" in root


def test_get_filesystem_unknown_protocol_missing_fsspec(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "fsspec", None)
    with pytest.raises(ImportError, match="fsspec"):
        get_filesystem("gs://bucket/data")


def test_list_files_local(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    (tmp_path / "b.csv").write_text("x")
    (tmp_path / "subdir").mkdir()
    files = list_files(None, str(tmp_path))
    assert [f.split("/")[-1] for f in files] == ["a.csv", "b.csv"]


def test_list_files_memory(memfs):
    memfs.mkdir("bucket/data", create_parents=True)
    memfs.open("bucket/data/a.csv", "w").write("x")
    memfs.open("bucket/data/b.csv", "w").write("x")
    files = list_files(memfs, "bucket/data")
    assert [f.split("/")[-1] for f in files] == ["a.csv", "b.csv"]


def test_file_basename():
    assert file_basename("/local/path/file.csv") == "file.csv"
    assert file_basename("bucket/prefix/file.csv") == "file.csv"


def test_compute_hash_memory(memfs):
    memfs.open("bucket/file.txt", "wb").write(b"hello world")
    h = compute_hash("bucket/file.txt", fs=memfs)
    assert len(h) == 64
    # same content via local file gives same hash
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"hello world")
        tmp_path = tmp.name
    try:
        assert compute_hash(tmp_path) == h
    finally:
        os.unlink(tmp_path)


def test_load_file_from_memory_fs(memfs, config, tmp_path):
    memfs.open("bucket/data/items.csv", "w").write("name,value\nfoo,bar\nbaz,qux\n")

    connector = SQLiteConnector(
        url=f"sqlite:///{tmp_path}/test.db", write_mode="append", batch_size=100
    )
    connector.ensure_table(config)

    rows, error = load_file(connector, config, "bucket/data/items.csv", "h1", fs=memfs)
    assert error is None
    assert rows == 2

    count = connector._get_conn().execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 2
    connector.close()
