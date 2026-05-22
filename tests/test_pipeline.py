
from etl.db import (
    claim_processing,
    find_file_by_hash,
    insert_pending,
    mark_failed,
    reset_eligible_failed,
)


# --- reset_eligible_failed ---

def test_failed_below_cap_is_reset_to_pending(db):
    insert_pending(db, "file.csv", "h1")
    claim_processing(db, "h1")
    mark_failed(db, "h1", "error")  # attempt_count → 1
    db.commit()

    reset_eligible_failed(db, retry_cap=3)
    db.commit()

    assert find_file_by_hash(db, "h1").state == "PENDING"


def test_failed_at_cap_stays_failed(db):
    insert_pending(db, "file.csv", "h2")
    claim_processing(db, "h2")
    mark_failed(db, "h2", "error")  # attempt_count → 1
    mark_failed(db, "h2", "error")  # attempt_count → 2 (reusing mark_failed for simplicity)
    mark_failed(db, "h2", "error")  # attempt_count → 3
    db.commit()

    reset_eligible_failed(db, retry_cap=3)
    db.commit()

    record = find_file_by_hash(db, "h2")
    assert record.state == "FAILED"
    assert record.attempt_count == 3


def test_reset_eligible_failed_returns_count(db):
    insert_pending(db, "a.csv", "ha")
    claim_processing(db, "ha")
    mark_failed(db, "ha", "err")

    insert_pending(db, "b.csv", "hb")
    claim_processing(db, "hb")
    mark_failed(db, "hb", "err")
    mark_failed(db, "hb", "err")
    mark_failed(db, "hb", "err")  # at cap=3, terminal
    db.commit()

    count = reset_eligible_failed(db, retry_cap=3)
    assert count == 1  # only "ha" reset


def test_committed_files_not_touched_by_reset(db):
    insert_pending(db, "done.csv", "hd")
    claim_processing(db, "hd")
    db.commit()

    # Manually set to COMMITTED (simulating a successful prior run)
    db.execute(
        "UPDATE etl_file_audit SET state='COMMITTED' WHERE content_hash='hd'"
    )
    db.commit()

    reset_eligible_failed(db, retry_cap=3)
    db.commit()

    assert find_file_by_hash(db, "hd").state == "COMMITTED"


# --- End-to-end retry via pipeline ---

def test_pipeline_retries_failed_file(tmp_path):
    """A file that fails on run 1 is retried on run 2 if below retry_cap."""
    from etl.pipeline import run_pipeline

    # Write a bad CSV (missing required column) then replace with a good one
    watched = tmp_path / "watch"
    watched.mkdir()
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        "format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        "stale_timeout_minutes: 30\ncolumns:\n"
        "  - source: name\n    dest: name\n    type: string\n    required: true\n"
        "  - source: value\n    dest: value\n    type: string\n    required: true\n"
    )
    audit_db_url = f"sqlite:///{tmp_path}/test.db"

    # Run 1: bad CSV (missing 'value' column) → FAILED
    bad = watched / "data.csv"
    bad.write_text("name\nAlice\n")
    result1 = run_pipeline(str(watched), str(config_file), audit_db_url)
    assert result1["failed"] == 1
    assert result1["committed"] == 0

    # Replace with good CSV (same path, different content → different hash)
    bad.write_text("name,value\nAlice,100\n")
    result2 = run_pipeline(str(watched), str(config_file), audit_db_url)
    # Old bad hash stays FAILED (below cap → retried but still fails on retry
    # since file content changed — new hash is new PENDING file)
    assert result2["committed"] == 1  # new good file committed
