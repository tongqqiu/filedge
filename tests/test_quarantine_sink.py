"""The Quarantine sink buffers bad rows and writes an NDJSON sidecar only on
finalize. Nothing is written until then, so a discarded (wholesale-failed) File
leaves no sidecar behind.
"""

import json
import os


from filedge.quarantine.sink import QuarantineSink


def _sink(tmp_path):
    return QuarantineSink(str(tmp_path / "quarantine"), "orders-2026-05-30.csv", "abc123def456ghi")


def test_record_then_finalize_writes_ndjson_sidecar(tmp_path):
    sink = _sink(tmp_path)
    sink.record(3, "amount", "cannot coerce 'n/a' to float", {"id": "3", "amount": "n/a"})
    sink.record(7, "id", "Required column 'id' is empty", {"id": "", "amount": "5"})

    path = sink.finalize()

    assert os.path.isfile(path)
    lines = [json.loads(line) for line in open(path).read().splitlines()]
    assert lines == [
        {"row_number": 3, "column": "amount",
         "error": "cannot coerce 'n/a' to float", "row": {"id": "3", "amount": "n/a"}},
        {"row_number": 7, "column": "id",
         "error": "Required column 'id' is empty", "row": {"id": "", "amount": "5"}},
    ]


def test_count_tracks_recorded_rows(tmp_path):
    sink = _sink(tmp_path)
    assert sink.count == 0
    sink.record(1, "c", "e", {"c": "x"})
    sink.record(2, "c", "e", {"c": "y"})
    assert sink.count == 2


def test_nothing_written_until_finalize(tmp_path):
    qdir = tmp_path / "quarantine"
    sink = QuarantineSink(str(qdir), "f.csv", "h")
    sink.record(1, "c", "e", {"c": "x"})

    # No finalize call yet — the directory should not even exist.
    assert not qdir.exists()


def test_discard_leaves_no_sidecar(tmp_path):
    qdir = tmp_path / "quarantine"
    sink = QuarantineSink(str(qdir), "f.csv", "h")
    sink.record(1, "c", "e", {"c": "x"})

    sink.discard()

    assert sink.count == 0
    assert not qdir.exists()


def test_sidecar_name_ties_to_source_and_hash(tmp_path):
    sink = QuarantineSink(str(tmp_path), "orders-2026-05-30.csv", "abc123def456ghi789")
    name = sink.sidecar_name()
    assert name == "orders-2026-05-30.abc123def456.quarantine.ndjson"


def test_finalize_with_no_rows_writes_empty_sidecar(tmp_path):
    sink = _sink(tmp_path)
    path = sink.finalize()
    assert os.path.isfile(path)
    assert open(path).read() == ""
