from filedge.db import (
    claim_processing,
    find_file_by_hash,
    find_terminal_failed_by_filename,
    get_status_summary,
    insert_pending,
    list_terminal_failed,
    mark_committed,
    mark_failed,
    reclaim_stale_processing,
    requeue_all_terminal_failed,
    requeue_by_hash,
)


def _audit_columns(db) -> set[str]:
    cursor = db.execute("PRAGMA table_info(etl_file_audit)")
    return {row[1] for row in cursor.fetchall()}


def test_count_stale_processing_returns_zero_when_no_processing_rows(db):
    from filedge.db import count_stale_processing
    assert count_stale_processing(db, stale_minutes=30) == 0


def test_count_stale_processing_is_read_only_and_counts_old_locks(db, tmp_path):
    """count_stale_processing must not mutate state — repeated calls return the same."""
    import datetime
    from filedge.db import (
        Database, claim_processing, count_stale_processing,
        create_audit_tables, find_file_by_hash, insert_pending,
    )

    db2 = Database(f"sqlite:///{tmp_path}/count_stale.db")
    create_audit_tables(db2)
    insert_pending(db2, "fresh.csv", "h-fresh")
    insert_pending(db2, "stale.csv", "h-stale")
    claim_processing(db2, "h-fresh")
    claim_processing(db2, "h-stale")
    # Backdate the stale row's claimed_at by 2 hours.
    long_ago = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    db2.execute(
        "UPDATE etl_file_audit SET claimed_at=? WHERE content_hash=?",
        [long_ago, "h-stale"],
    )
    db2.commit()

    # First call: 1 stale row at 30-min threshold.
    assert count_stale_processing(db2, stale_minutes=30) == 1
    # Second call same answer — read-only.
    assert count_stale_processing(db2, stale_minutes=30) == 1
    # PROCESSING rows unchanged.
    assert find_file_by_hash(db2, "h-stale").state == "PROCESSING"
    assert find_file_by_hash(db2, "h-fresh").state == "PROCESSING"
    db2.close()


def test_create_audit_tables_adds_run_id_column(db):
    assert "run_id" in _audit_columns(db)


def test_create_audit_tables_migrates_existing_table_without_run_id(tmp_path):
    """An audit DB written by an older filedge (no run_id column) must be upgraded
    by create_audit_tables() without losing rows."""
    from filedge.db import Database, create_audit_tables, find_file_by_hash

    db_path = tmp_path / "legacy.db"
    legacy = Database(f"sqlite:///{db_path}")
    legacy.execute(
        "CREATE TABLE etl_file_audit ("
        "id INTEGER PRIMARY KEY, filename TEXT NOT NULL, source_dir TEXT,"
        " content_hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL,"
        " attempt_count INTEGER NOT NULL DEFAULT 0, error_message TEXT,"
        " worker_id TEXT, claimed_at TEXT, created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO etl_file_audit (filename, content_hash, state, created_at, updated_at)"
        " VALUES ('legacy.csv', 'legacy-hash', 'COMMITTED', '2026-01-01', '2026-01-01')"
    )
    legacy.commit()
    legacy.close()

    upgraded = Database(f"sqlite:///{db_path}")
    create_audit_tables(upgraded)

    cursor = upgraded.execute("PRAGMA table_info(etl_file_audit)")
    assert "run_id" in {row[1] for row in cursor.fetchall()}
    legacy_row = find_file_by_hash(upgraded, "legacy-hash")
    assert legacy_row is not None and legacy_row.filename == "legacy.csv"
    upgraded.close()


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
    claimed = claim_processing(db, "h1", worker_id="worker-a")
    db.commit()

    record = find_file_by_hash(db, "h1")
    assert claimed is True
    assert record.state == "PROCESSING"
    assert record.worker_id == "worker-a"

    mark_committed(db, "h1")
    db.commit()

    record = find_file_by_hash(db, "h1")
    assert record.state == "COMMITTED"
    assert record.worker_id is None


def test_failure_state_machine(db):
    insert_pending(db, "file.csv", "h2")
    claim_processing(db, "h2", worker_id="worker-b")
    mark_failed(db, "h2", "bad data on row 3")
    db.commit()

    record = find_file_by_hash(db, "h2")
    assert record.state == "FAILED"
    assert record.error_message == "bad data on row 3"
    assert record.worker_id is None
    assert record.attempt_count == 1


def test_claim_processing_is_conditional_on_pending(db):
    insert_pending(db, "file.csv", "h5")
    assert claim_processing(db, "h5", worker_id="first") is True
    assert claim_processing(db, "h5", worker_id="second") is False
    db.commit()

    record = find_file_by_hash(db, "h5")
    assert record.state == "PROCESSING"
    assert record.worker_id == "first"


def test_reclaim_stale_processing_clears_worker_id(db):
    insert_pending(db, "file.csv", "h6")
    claim_processing(db, "h6", worker_id="stale-worker")
    db.execute(
        "UPDATE etl_file_audit SET claimed_at='2000-01-01T00:00:00+00:00'"
        " WHERE content_hash='h6'"
    )
    db.commit()

    assert reclaim_stale_processing(db, stale_minutes=1) == 1
    db.commit()

    record = find_file_by_hash(db, "h6")
    assert record.state == "PENDING"
    assert record.worker_id is None


def test_insert_pending_stores_source_dir(db):
    insert_pending(db, "orders.csv", "h_sd", source_dir="/data/incoming")
    db.commit()
    record = find_file_by_hash(db, "h_sd")
    assert record.source_dir == "/data/incoming"


def test_insert_pending_source_dir_defaults_to_none(db):
    insert_pending(db, "orders.csv", "h_sd2")
    db.commit()
    record = find_file_by_hash(db, "h_sd2")
    assert record.source_dir is None


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
    assert summary["recent_failures"][0]["content_hash"] == "h4"
    assert summary["recent_failures"][0]["error_message"] == "parse error"


def _make_terminal_failed(db, filename, content_hash, retry_cap=3):
    insert_pending(db, filename, content_hash)
    for _ in range(retry_cap):
        claim_processing(db, content_hash)
        mark_failed(db, content_hash, "persistent error")
    db.commit()


def test_find_terminal_failed_by_filename_returns_match(db):
    _make_terminal_failed(db, "orders.csv", "term1")
    records = find_terminal_failed_by_filename(db, "orders.csv", retry_cap=3)
    assert len(records) == 1
    assert records[0].content_hash == "term1"
    assert records[0].state == "FAILED"
    assert records[0].attempt_count == 3


def test_find_terminal_failed_by_filename_returns_multiple(db):
    _make_terminal_failed(db, "orders.csv", "hashA")
    _make_terminal_failed(db, "orders.csv", "hashB")
    records = find_terminal_failed_by_filename(db, "orders.csv", retry_cap=3)
    hashes = {r.content_hash for r in records}
    assert hashes == {"hashA", "hashB"}


def test_find_terminal_failed_by_filename_ignores_non_terminal(db):
    insert_pending(db, "orders.csv", "non_term")
    claim_processing(db, "non_term")
    mark_failed(db, "non_term", "err")  # attempt_count=1 < retry_cap=3
    db.commit()
    records = find_terminal_failed_by_filename(db, "orders.csv", retry_cap=3)
    assert records == []


def test_find_terminal_failed_by_filename_no_match(db):
    records = find_terminal_failed_by_filename(db, "missing.csv", retry_cap=3)
    assert records == []


def test_list_terminal_failed_returns_all(db):
    _make_terminal_failed(db, "a.csv", "ta")
    _make_terminal_failed(db, "b.csv", "tb")
    insert_pending(db, "c.csv", "tc")  # PENDING — excluded
    records = list_terminal_failed(db, retry_cap=3)
    hashes = {r.content_hash for r in records}
    assert hashes == {"ta", "tb"}


def test_requeue_by_hash_resets_state_and_budget(db):
    _make_terminal_failed(db, "orders.csv", "rq1")
    requeue_by_hash(db, "rq1")
    db.commit()
    record = find_file_by_hash(db, "rq1")
    assert record.state == "PENDING"
    assert record.attempt_count == 0
    assert record.error_message is None
    assert record.worker_id is None


def test_requeue_all_terminal_failed_resets_count(db):
    _make_terminal_failed(db, "a.csv", "ra")
    _make_terminal_failed(db, "b.csv", "rb")
    insert_pending(db, "c.csv", "rc")  # PENDING — untouched
    n = requeue_all_terminal_failed(db, retry_cap=3)
    db.commit()
    assert n == 2
    assert find_file_by_hash(db, "ra").state == "PENDING"
    assert find_file_by_hash(db, "rb").state == "PENDING"
    assert find_file_by_hash(db, "rc").state == "PENDING"  # already PENDING


def test_requeue_all_terminal_failed_returns_zero_when_none(db):
    n = requeue_all_terminal_failed(db, retry_cap=3)
    db.commit()
    assert n == 0

