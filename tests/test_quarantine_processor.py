"""The quarantining row processor: good rows pass through, bad rows are captured,
and the threshold raises at end-of-stream (whole-File rollback) when exceeded.
Driven by plain row iterators + a real sink on a temp dir — no Connector/DB.
"""

import pytest

from filedge.config import ColumnMapping, QuarantineConfig
from filedge.quarantine.processor import QuarantineThresholdExceeded, quarantining_rows
from filedge.quarantine.sink import QuarantineSink
from filedge.transform import TransformError, transform_row

_COLUMNS = [
    ColumnMapping(source="id", dest="id", type="integer", required=True),
    ColumnMapping(source="amount", dest="amount", type="float", required=True),
]


def _sink(tmp_path):
    return QuarantineSink(str(tmp_path / "q"), "orders.csv", "hash123456789")


def _rows(*rows):
    return iter(list(rows))


def test_good_rows_pass_through_transformed(tmp_path):
    q = QuarantineConfig(dir="q", max_invalid_rows=10)
    out = list(quarantining_rows(
        _rows({"id": "1", "amount": "9.5"}, {"id": "2", "amount": "3.0"}),
        _COLUMNS, q, _sink(tmp_path),
    ))
    assert out == [{"id": 1, "amount": 9.5}, {"id": 2, "amount": 3.0}]


def test_bad_row_is_captured_and_not_yielded(tmp_path):
    q = QuarantineConfig(dir="q", max_invalid_rows=10)
    sink = _sink(tmp_path)
    out = list(quarantining_rows(
        _rows(
            {"id": "1", "amount": "9.5"},
            {"id": "2", "amount": "n/a"},   # bad: not a float
            {"id": "3", "amount": "4.0"},
        ),
        _COLUMNS, q, sink,
    ))
    assert out == [{"id": 1, "amount": 9.5}, {"id": 3, "amount": 4.0}]
    assert sink.count == 1
    path = sink.finalize()
    import json
    rec = json.loads(open(path).read().splitlines()[0])
    assert rec["row_number"] == 2
    assert rec["column"] == "amount"
    assert "amount" in rec["error"]
    assert rec["row"] == {"id": "2", "amount": "n/a"}


def test_under_threshold_completes_and_yields_good_rows(tmp_path):
    q = QuarantineConfig(dir="q", max_invalid_fraction=0.5)
    sink = _sink(tmp_path)
    # 1 bad of 4 = 25% <= 50%
    out = list(quarantining_rows(
        _rows(
            {"id": "1", "amount": "1"}, {"id": "x", "amount": "2"},  # bad id
            {"id": "3", "amount": "3"}, {"id": "4", "amount": "4"},
        ),
        _COLUMNS, q, sink,
    ))
    assert len(out) == 3
    assert sink.count == 1


def test_over_threshold_raises_and_discards_sink(tmp_path):
    q = QuarantineConfig(dir="q", max_invalid_rows=1)
    sink = _sink(tmp_path)
    gen = quarantining_rows(
        _rows(
            {"id": "1", "amount": "1"},
            {"id": "2", "amount": "bad"},   # invalid #1
            {"id": "3", "amount": "bad"},   # invalid #2 → over max_invalid_rows=1
        ),
        _COLUMNS, q, sink,
    )
    with pytest.raises(QuarantineThresholdExceeded):
        list(gen)
    assert sink.count == 0  # discarded — nothing to finalize


def test_threshold_boundary_exactly_at_limit_is_ok(tmp_path):
    q = QuarantineConfig(dir="q", max_invalid_rows=2)
    sink = _sink(tmp_path)
    out = list(quarantining_rows(
        _rows(
            {"id": "1", "amount": "1"},
            {"id": "2", "amount": "bad"},   # invalid #1
            {"id": "3", "amount": "bad"},   # invalid #2 == limit (not over)
        ),
        _COLUMNS, q, sink,
    ))
    assert out == [{"id": 1, "amount": 1.0}]
    assert sink.count == 2  # both quarantined, File still committed


def test_post_transform_failure_is_quarantined(tmp_path):
    q = QuarantineConfig(dir="q", max_invalid_rows=10)
    sink = _sink(tmp_path)

    def explode(row):
        from filedge.field_crypto import FieldCryptoError
        if row["id"] == 2:
            raise FieldCryptoError("encryption failed")
        return row

    out = list(quarantining_rows(
        _rows({"id": "1", "amount": "1"}, {"id": "2", "amount": "2"}),
        _COLUMNS, q, sink, post_transform=explode,
    ))
    assert out == [{"id": 1, "amount": 1.0}]
    assert sink.count == 1


def test_strict_baseline_transform_row_still_raises_on_first_bad_row():
    # The quarantine-disabled path (load_file uses transform_row directly) is
    # unchanged: the first bad row raises (whole-File failure, ADR-0003).
    with pytest.raises(TransformError):
        transform_row({"id": "x", "amount": "1"}, _COLUMNS)
