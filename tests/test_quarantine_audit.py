"""The Audit Record carries quarantined_row_count (+ sidecar path) so a partial
commit is explicit. State stays COMMITTED; existing audit DBs migrate cleanly.
"""

from filedge.db import (
    Database,
    create_audit_tables,
    find_file_by_hash,
    insert_pending,
    mark_committed,
)


def _audit_columns(db) -> set:
    cursor = db.execute("PRAGMA table_info(etl_file_audit)")
    return {row[1] for row in cursor.fetchall()}


def test_audit_table_has_quarantine_columns(db):
    cols = _audit_columns(db)
    assert "quarantined_row_count" in cols
    assert "quarantine_path" in cols


def test_mark_committed_records_quarantine_counts(db):
    insert_pending(db, "orders.csv", "h1")
    mark_committed(db, "h1", row_count=98, quarantined_row_count=2,
                   quarantine_path="/q/orders.h1.quarantine.ndjson")
    db.commit()

    rec = find_file_by_hash(db, "h1")
    assert rec.state == "COMMITTED"        # no new state
    assert rec.row_count == 98
    assert rec.quarantined_row_count == 2
    assert rec.quarantine_path == "/q/orders.h1.quarantine.ndjson"


def test_clean_commit_defaults_to_zero_quarantine(db):
    insert_pending(db, "clean.csv", "h2")
    mark_committed(db, "h2", row_count=100)  # existing callers unaffected
    db.commit()

    rec = find_file_by_hash(db, "h2")
    assert rec.row_count == 100
    assert rec.quarantined_row_count == 0
    assert rec.quarantine_path is None


def test_legacy_audit_db_migrates_quarantine_columns(tmp_path):
    # An audit DB created without the quarantine columns gains them on re-open.
    url = f"sqlite:///{tmp_path}/legacy.db"
    db = Database(url)
    db.execute(
        "CREATE TABLE etl_file_audit ("
        " id INTEGER PRIMARY KEY, filename TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,"
        " state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.commit()

    create_audit_tables(db)  # idempotent migration

    cols = _audit_columns(db)
    assert "quarantined_row_count" in cols
    assert "quarantine_path" in cols
    db.close()
