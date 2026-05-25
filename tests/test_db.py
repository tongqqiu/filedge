from filedge.db import (
    Database,
    claim_processing,
    create_audit_tables,
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


def test_create_audit_tables_adds_run_id_column(db):
    assert "run_id" in _audit_columns(db)


def test_create_audit_tables_adds_source_manifest_columns(db):
    columns = _audit_columns(db)
    assert "source_type" in columns
    assert "source_name" in columns
    assert "producer" in columns
    assert "external_run_id" in columns
    assert "manifest_payload" in columns


def test_create_audit_tables_migrates_legacy_table_to_add_source_manifest_columns(tmp_path):
    """An audit DB written by an older filedge (no source-manifest columns) must be
    upgraded by create_audit_tables() without losing rows."""
    db_path = tmp_path / "legacy.db"
    legacy = Database(f"sqlite:///{db_path}")
    legacy.execute(
        "CREATE TABLE etl_file_audit ("
        "id INTEGER PRIMARY KEY, filename TEXT NOT NULL, source_dir TEXT,"
        " content_hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL,"
        " attempt_count INTEGER NOT NULL DEFAULT 0, error_message TEXT,"
        " worker_id TEXT, run_id TEXT, row_count INTEGER, claimed_at TEXT,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO etl_file_audit (filename, content_hash, state, created_at, updated_at)"
        " VALUES ('old.csv', 'old-hash', 'COMMITTED', '2026-01-01', '2026-01-01')"
    )
    legacy.commit()
    legacy.close()

    upgraded = Database(f"sqlite:///{db_path}")
    create_audit_tables(upgraded)

    cursor = upgraded.execute("PRAGMA table_info(etl_file_audit)")
    columns = {row[1] for row in cursor.fetchall()}
    assert {"source_type", "source_name", "producer", "external_run_id", "manifest_payload"} <= columns
    legacy_row = find_file_by_hash(upgraded, "old-hash")
    assert legacy_row is not None and legacy_row.filename == "old.csv"
    upgraded.close()


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


def test_insert_pending_stores_source_metadata(db):
    from filedge.source_manifest import SourceMetadata
    metadata = SourceMetadata(
        source_type="api",
        source_name="stripe.charges",
        producer="https://github.com/dlt-hub/dlt",
        external_run_id="dlt-run-1",
        raw_payload='{"eventType":"COMPLETE"}',
    )
    insert_pending(db, "stripe.ndjson", "h-stripe", source_metadata=metadata)
    db.commit()

    record = find_file_by_hash(db, "h-stripe")
    assert record.source_type == "api"
    assert record.source_name == "stripe.charges"
    assert record.producer == "https://github.com/dlt-hub/dlt"
    assert record.external_run_id == "dlt-run-1"
    assert record.manifest_payload == '{"eventType":"COMPLETE"}'


def test_insert_pending_without_source_metadata_leaves_columns_null(db):
    insert_pending(db, "direct.csv", "h-direct")
    db.commit()

    record = find_file_by_hash(db, "h-direct")
    assert record.source_type is None
    assert record.source_name is None
    assert record.producer is None
    assert record.external_run_id is None
    assert record.manifest_payload is None


def test_migration_adds_row_count_to_existing_table(tmp_path):
    """An audit DB without row_count must be upgraded without losing data."""
    db_path = tmp_path / "no_row_count.db"
    old = Database(f"sqlite:///{db_path}")
    old.execute(
        "CREATE TABLE etl_file_audit ("
        "id INTEGER PRIMARY KEY, filename TEXT NOT NULL, source_dir TEXT,"
        " content_hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL,"
        " attempt_count INTEGER NOT NULL DEFAULT 0, error_message TEXT,"
        " worker_id TEXT, run_id TEXT, claimed_at TEXT,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    old.execute(
        "INSERT INTO etl_file_audit (filename, content_hash, state, created_at, updated_at)"
        " VALUES ('orders.csv', 'hash-old', 'COMMITTED', '2026-01-01', '2026-01-01')"
    )
    old.commit()
    old.close()

    upgraded = Database(f"sqlite:///{db_path}")
    create_audit_tables(upgraded)

    columns = _audit_columns(upgraded)
    assert "row_count" in columns
    record = find_file_by_hash(upgraded, "hash-old")
    assert record is not None and record.filename == "orders.csv"
    assert record.row_count is None
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


def test_mark_committed_persists_row_count(db):
    insert_pending(db, "file.csv", "h1rc")
    claim_processing(db, "h1rc")
    mark_committed(db, "h1rc", row_count=1500)
    db.commit()

    record = find_file_by_hash(db, "h1rc")
    assert record.row_count == 1500


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

