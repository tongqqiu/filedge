from filedge.audit_records import (
    LineageAmbiguous,
    LineageFound,
    LineageMissing,
    export_records,
    lineage_record,
    status_summary,
)
from filedge.db import claim_processing, insert_pending, mark_committed, mark_failed


def test_status_summary_counts_states_and_recent_failures(db):
    insert_pending(db, "pending.csv", "hash-pending")

    insert_pending(db, "processing.csv", "hash-processing")
    claim_processing(db, "hash-processing")

    insert_pending(db, "done.csv", "hash-done")
    claim_processing(db, "hash-done")
    mark_committed(db, "hash-done", row_count=12)

    insert_pending(db, "broken.csv", "hash-broken")
    claim_processing(db, "hash-broken")
    mark_failed(db, "hash-broken", "missing amount")
    db.commit()

    summary = status_summary(db)

    assert summary["PENDING"] == 1
    assert summary["PROCESSING"] == 1
    assert summary["COMMITTED"] == 1
    assert summary["FAILED"] == 1
    assert summary["recent_failures"] == [
        {
            "filename": "broken.csv",
            "content_hash": "hash-broken",
            "error_message": "missing amount",
            "source_type": None,
            "source_name": None,
            "producer": None,
            "external_run_id": None,
        }
    ]


def test_export_records_shape_matches_audit_export_table(db):
    insert_pending(db, "older.csv", "hash-old", source_dir="/landing")
    mark_failed(db, "hash-old", "bad row")
    db.execute(
        "UPDATE etl_file_audit SET updated_at = ? WHERE content_hash = ?",
        ["2026-05-24T10:00:00+00:00", "hash-old"],
    )

    insert_pending(db, "newer.csv", "hash-new")
    claim_processing(db, "hash-new")
    mark_committed(db, "hash-new", row_count=42)
    db.execute(
        "UPDATE etl_file_audit SET updated_at = ? WHERE content_hash = ?",
        ["2026-05-25T10:00:00+00:00", "hash-new"],
    )
    db.commit()

    records = export_records(db)

    assert [record.filename for record in records] == ["newer.csv", "older.csv"]
    assert records[0].content_hash == "hash-new"
    assert records[0].state == "COMMITTED"
    assert records[0].row_count == 42
    assert records[1].source_dir == "/landing"
    assert records[1].error_message == "bad row"


def test_lineage_record_resolves_hash_filename_missing_and_ambiguous(db):
    insert_pending(db, "unique.csv", "hash-unique")
    insert_pending(db, "shared.csv", "hash-shared-1")
    insert_pending(db, "shared.csv", "hash-shared-2")
    claim_processing(db, "hash-unique", run_id="run-123")
    mark_committed(db, "hash-unique", row_count=7)
    db.commit()

    by_hash = lineage_record(db, "hash-unique")
    assert isinstance(by_hash, LineageFound)
    assert by_hash.record.filename == "unique.csv"
    assert by_hash.run_id == "run-123"
    assert by_hash.created_at is not None
    assert by_hash.updated_at is not None

    by_filename = lineage_record(db, "unique.csv")
    assert isinstance(by_filename, LineageFound)
    assert by_filename.record.content_hash == "hash-unique"

    missing = lineage_record(db, "not-here")
    assert isinstance(missing, LineageMissing)
    assert missing.identifier == "not-here"

    ambiguous = lineage_record(db, "shared.csv")
    assert isinstance(ambiguous, LineageAmbiguous)
    assert [match.content_hash for match in ambiguous.matches] == [
        "hash-shared-1",
        "hash-shared-2",
    ]
