import pytest

from etl.config import ColumnMapping, PipelineConfig
from etl.db import (
    SchemaError,
    claim_processing,
    ensure_destination_table,
    find_file_by_hash,
    get_status_summary,
    insert_pending,
    mark_committed,
    mark_failed,
    reclaim_stale_processing,
)


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="test_orders",
        columns=[ColumnMapping(source="name", dest="name", type="string", required=True)],
    )


def test_insert_and_find_pending(db):
    insert_pending(db, "orders.csv", "abc123")
    db.commit()

    record = find_file_by_hash(db, "abc123")
    assert record is not None
    assert record.filename == "orders.csv"
    assert record.state == "PENDING"
    assert record.attempt_count == 0


def test_find_nonexistent_returns_none(db):
    assert find_file_by_hash(db, "doesnotexist") is None


def test_success_state_machine(db):
    insert_pending(db, "file.csv", "h1")
    claim_processing(db, "h1")
    db.commit()

    assert find_file_by_hash(db, "h1").state == "PROCESSING"

    mark_committed(db, "h1")
    db.commit()

    assert find_file_by_hash(db, "h1").state == "COMMITTED"


def test_failure_state_machine(db):
    insert_pending(db, "file.csv", "h2")
    claim_processing(db, "h2")
    mark_failed(db, "h2", "bad data on row 3")
    db.commit()

    record = find_file_by_hash(db, "h2")
    assert record.state == "FAILED"
    assert record.error_message == "bad data on row 3"
    assert record.attempt_count == 1


def test_duplicate_hash_insert_is_noop(db):
    insert_pending(db, "first.csv", "samehash")
    db.commit()
    insert_pending(db, "second.csv", "samehash")
    db.commit()

    record = find_file_by_hash(db, "samehash")
    assert record.filename == "first.csv"  # first insert wins


def test_get_status_summary(db):
    insert_pending(db, "a.csv", "h1")
    db.commit()

    insert_pending(db, "b.csv", "h2")
    claim_processing(db, "h2")
    db.commit()

    insert_pending(db, "c.csv", "h3")
    claim_processing(db, "h3")
    mark_committed(db, "h3")
    db.commit()

    insert_pending(db, "d.csv", "h4")
    claim_processing(db, "h4")
    mark_failed(db, "h4", "parse error")
    db.commit()

    summary = get_status_summary(db)
    assert summary["PENDING"] == 1
    assert summary["PROCESSING"] == 1
    assert summary["COMMITTED"] == 1
    assert summary["FAILED"] == 1
    assert len(summary["recent_failures"]) == 1
    assert summary["recent_failures"][0]["filename"] == "d.csv"
    assert summary["recent_failures"][0]["error_message"] == "parse error"


def test_ensure_destination_table_creates_with_provenance_columns(db, config):
    ensure_destination_table(db, config)
    db.commit()

    cursor = db.execute("PRAGMA table_info(test_orders)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "name" in cols
    assert "_source_file_hash" in cols
    assert "_ingested_at" in cols


def test_ensure_destination_table_is_idempotent(db, config):
    ensure_destination_table(db, config)
    db.commit()
    ensure_destination_table(db, config)  # must not raise


def test_ensure_destination_table_raises_on_missing_column(db, config):
    ensure_destination_table(db, config)
    db.commit()

    config.columns.append(
        ColumnMapping(source="new_col", dest="new_col", type="string", required=True)
    )
    with pytest.raises(SchemaError, match="new_col"):
        ensure_destination_table(db, config)
