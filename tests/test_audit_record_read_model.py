from filedge.audit_records import (
    LineageAmbiguous,
    LineageFound,
    LineageMissing,
    RequeueAmbiguous,
    RequeueNotEligible,
    RequeueNotFound,
    Requeued,
    export_records,
    lineage_record,
    requeue_file,
    status_summary,
)
from filedge.db import (
    FileState,
    FileRecord,
    claim_processing,
    find_file_by_hash,
    insert_pending,
    is_terminal_failed,
    mark_committed,
    mark_failed,
)


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


# --- Requeue Eligibility predicate ---------------------------------------------

def _record(state: str, attempt_count: int) -> FileRecord:
    return FileRecord(
        id=1, filename="f.csv", source_dir=None, content_hash="h",
        state=state, attempt_count=attempt_count, error_message=None,
        worker_id=None, claimed_at=None,
    )


def test_is_terminal_failed_at_and_above_cap():
    assert is_terminal_failed(_record(FileState.FAILED, 3), retry_cap=3) is True
    assert is_terminal_failed(_record(FileState.FAILED, 5), retry_cap=3) is True


def test_is_terminal_failed_false_below_cap_or_other_state():
    assert is_terminal_failed(_record(FileState.FAILED, 2), retry_cap=3) is False
    assert is_terminal_failed(_record(FileState.COMMITTED, 9), retry_cap=3) is False
    assert is_terminal_failed(_record(FileState.PENDING, 9), retry_cap=3) is False


# --- requeue_file use-case -----------------------------------------------------

def _make_terminal_failed(db, filename, content_hash, retry_cap=3):
    insert_pending(db, filename, content_hash)
    claim_processing(db, content_hash)
    for _ in range(retry_cap):
        mark_failed(db, content_hash, "boom")
    db.commit()


def test_requeue_file_by_hash_resets_to_pending(db):
    _make_terminal_failed(db, "orders.csv", "h1")

    outcome = requeue_file(db, retry_cap=3, content_hash="h1")

    assert isinstance(outcome, Requeued)
    assert outcome.record.content_hash == "h1"
    after = find_file_by_hash(db, "h1")
    assert after.state == FileState.PENDING
    assert after.attempt_count == 0


def test_requeue_file_by_hash_missing_is_not_found(db):
    outcome = requeue_file(db, retry_cap=3, content_hash="nope")
    assert outcome == RequeueNotFound(target="nope")


def test_requeue_file_by_hash_below_cap_is_not_eligible(db):
    insert_pending(db, "orders.csv", "h2")
    claim_processing(db, "h2")
    mark_failed(db, "h2", "boom")  # attempt_count=1, below cap
    db.commit()

    outcome = requeue_file(db, retry_cap=3, content_hash="h2")

    assert isinstance(outcome, RequeueNotEligible)
    assert outcome.record.content_hash == "h2"
    assert find_file_by_hash(db, "h2").state == FileState.FAILED  # untouched


def test_requeue_file_by_filename_single_match(db):
    _make_terminal_failed(db, "orders.csv", "h3")

    outcome = requeue_file(db, retry_cap=3, filename="orders.csv")

    assert isinstance(outcome, Requeued)
    assert find_file_by_hash(db, "h3").state == FileState.PENDING


def test_requeue_file_by_filename_none_is_not_found(db):
    outcome = requeue_file(db, retry_cap=3, filename="missing.csv")
    assert outcome == RequeueNotFound(target="missing.csv")


def test_requeue_file_by_filename_multiple_is_ambiguous(db):
    _make_terminal_failed(db, "orders.csv", "hA")
    _make_terminal_failed(db, "orders.csv", "hB")

    outcome = requeue_file(db, retry_cap=3, filename="orders.csv")

    assert isinstance(outcome, RequeueAmbiguous)
    assert {r.content_hash for r in outcome.matches} == {"hA", "hB"}
    # Ambiguous resolution touches nothing.
    assert find_file_by_hash(db, "hA").state == FileState.FAILED
    assert find_file_by_hash(db, "hB").state == FileState.FAILED
