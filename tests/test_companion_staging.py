"""The staging writer materializes a window as one complete NDJSON File in the
staging area, named by source + cursor window, optionally gzipped.
"""

import gzip
import json
import os

from filedge.companion.staging import staged_filename, write_staged_ndjson


def test_writes_one_complete_ndjson_file(tmp_path):
    records = [{"id": 1}, {"id": 2}]
    staging = str(tmp_path / "staging")

    path = write_staged_ndjson(
        records, staging, "commits",
        from_cursor="2026-05-01", to_cursor="2026-05-02", timestamp="2026-05-30T00:00:00",
    )

    lines = open(path).read().splitlines()
    assert [json.loads(line) for line in lines] == records


def test_filename_encodes_source_and_window(tmp_path):
    path = write_staged_ndjson(
        [{"id": 1}], str(tmp_path / "staging"), "commits",
        from_cursor="2026-05-01", to_cursor="2026-05-02", timestamp="ts",
    )
    name = os.path.basename(path)
    assert name.startswith("commits-")
    assert "2026-05-01" in name and "2026-05-02" in name
    assert name.endswith(".ndjson")


def test_gzip_writes_compressed_readable_ndjson(tmp_path):
    records = [{"id": 1}]
    path = write_staged_ndjson(
        records, str(tmp_path / "staging"), "commits",
        from_cursor=None, to_cursor="x", timestamp="ts", gzip_enabled=True,
    )
    assert path.endswith(".ndjson.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        assert json.loads(f.read().strip()) == records[0]


def test_staged_filename_handles_a_none_cursor():
    name = staged_filename("commits", None, "x", "ts", gzip_enabled=False)
    assert "none" in name  # a first run (no from-cursor) still gets a stable name
