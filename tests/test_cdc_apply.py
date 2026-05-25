import pytest

from filedge.cdc import (
    CdcError,
    DEFAULT_OPERATION_MARKER_COLUMN,
    apply_transactional_cdc,
    plan_staged_cdc_records,
)
from filedge.config import CdcConfig


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def delete_by_key(self, table, key_columns, key_values):
        self.calls.append(("delete", table, tuple(key_columns), tuple(key_values)))

    def insert_row(self, table, columns, values):
        self.calls.append(("insert", table, tuple(columns), tuple(values)))


def _cdc(keys=("id",), sequence_by="seq") -> CdcConfig:
    return CdcConfig(
        operation_column="op",
        operations={"insert": ["I"], "update": ["U"], "delete": ["D"]},
        keys=list(keys),
        sequence_by=sequence_by,
    )


def test_apply_transactional_cdc_emits_delete_then_insert_for_updates():
    adapter = RecordingAdapter()
    rows = [
        {"id": 1, "name": "Ada", "op": "U", "seq": 5},
    ]

    apply_transactional_cdc(
        adapter,
        "customers",
        rows,
        file_hash="hash-1",
        ingested_at="2026-05-25T00:00:00+00:00",
        cdc=_cdc(),
    )

    assert adapter.calls == [
        ("delete", "customers", ("id",), (1,)),
        (
            "insert",
            "customers",
            ("id", "name", "seq", "_source_file_hash", "_ingested_at"),
            (1, "Ada", 5, "hash-1", "2026-05-25T00:00:00+00:00"),
        ),
    ]


def test_apply_transactional_cdc_emits_only_delete_for_delete_operation():
    adapter = RecordingAdapter()
    rows = [{"id": 7, "name": "gone", "op": "D", "seq": 3}]

    apply_transactional_cdc(
        adapter,
        "customers",
        rows,
        file_hash="hash-x",
        ingested_at="ts",
        cdc=_cdc(),
    )

    assert adapter.calls == [("delete", "customers", ("id",), (7,))]


def test_apply_transactional_cdc_collapses_to_latest_change_per_key():
    adapter = RecordingAdapter()
    rows = [
        {"id": 1, "name": "Ada", "op": "I", "seq": 1},
        {"id": 1, "name": "Ada v2", "op": "U", "seq": 2},
    ]

    apply_transactional_cdc(
        adapter, "customers", rows, file_hash="h", ingested_at="ts", cdc=_cdc()
    )

    operations = [call[0] for call in adapter.calls]
    assert operations == ["delete", "insert"]
    insert_values = adapter.calls[1][3]
    assert insert_values[1] == "Ada v2"


def test_apply_transactional_cdc_strips_operation_column_from_inserted_row():
    adapter = RecordingAdapter()
    rows = [{"id": 1, "name": "Ada", "op": "I", "seq": 1}]

    apply_transactional_cdc(
        adapter, "t", rows, file_hash="h", ingested_at="ts", cdc=_cdc()
    )

    insert_call = adapter.calls[-1]
    insert_columns = insert_call[2]
    assert "op" not in insert_columns


def test_apply_transactional_cdc_passes_composite_keys_in_declared_order():
    adapter = RecordingAdapter()
    rows = [
        {"tenant": "a", "id": 1, "name": "x", "op": "U", "seq": 1},
    ]

    apply_transactional_cdc(
        adapter,
        "t",
        rows,
        file_hash="h",
        ingested_at="ts",
        cdc=_cdc(keys=("tenant", "id")),
    )

    delete_call = adapter.calls[0]
    assert delete_call[2] == ("tenant", "id")
    assert delete_call[3] == ("a", 1)


def test_plan_staged_cdc_records_marks_operation_and_strips_operation_column():
    rows = [
        {"id": 1, "name": "Ada", "op": "U", "seq": 5},
        {"id": 2, "name": "Bea", "op": "D", "seq": 4},
    ]

    staged = plan_staged_cdc_records(rows, _cdc())

    assert staged.data_columns == ["id", "name", "seq"]
    assert staged.records == [
        {"id": 1, "name": "Ada", "seq": 5, DEFAULT_OPERATION_MARKER_COLUMN: "update"},
        {"id": 2, "name": "Bea", "seq": 4, DEFAULT_OPERATION_MARKER_COLUMN: "delete"},
    ]


def test_plan_staged_cdc_records_empty_for_empty_rows():
    staged = plan_staged_cdc_records([], _cdc())

    assert staged.records == []
    assert staged.data_columns == []


def test_plan_staged_cdc_records_collapses_to_latest_per_key():
    rows = [
        {"id": 1, "name": "Ada", "op": "I", "seq": 1},
        {"id": 1, "name": "Ada v2", "op": "U", "seq": 2},
    ]

    staged = plan_staged_cdc_records(rows, _cdc())

    assert len(staged.records) == 1
    record = staged.records[0]
    assert record["name"] == "Ada v2"
    assert record[DEFAULT_OPERATION_MARKER_COLUMN] == "update"


def test_plan_staged_cdc_records_supports_custom_marker_column():
    rows = [{"id": 1, "name": "Ada", "op": "I", "seq": 1}]

    staged = plan_staged_cdc_records(rows, _cdc(), operation_marker_column="_op")

    assert staged.records == [{"id": 1, "name": "Ada", "seq": 1, "_op": "insert"}]
    assert "_op" not in staged.data_columns


def test_apply_transactional_cdc_propagates_plan_errors():
    adapter = RecordingAdapter()
    rows = [{"id": 1, "name": "x", "op": "BOGUS", "seq": 1}]

    with pytest.raises(CdcError):
        apply_transactional_cdc(
            adapter, "t", rows, file_hash="h", ingested_at="ts", cdc=_cdc()
        )
    assert adapter.calls == []
