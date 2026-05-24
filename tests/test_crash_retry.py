"""
Automated check for the crash-retry demo (docs/guides/crash-retry.md).

Verifies that a stale PROCESSING lock is reclaimed, rows are not duplicated,
and filedge status returns to zero PROCESSING / one COMMITTED after recovery.
"""
import datetime
import sqlite3

import pytest

from filedge.db import Database, get_status_summary
from filedge.pipeline import run_pipeline


@pytest.fixture
def crash_retry_env(tmp_path):
    """Return (watched_dir, config_path, audit_db_url, dest_db_path)."""
    watched = tmp_path / "incoming"
    watched.mkdir()
    (watched / "orders.csv").write_text("order_id,amount\n1001,49.99\n1002,149.00\n")

    dest_db_url = f"sqlite:///{tmp_path}/orders.db"
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        f"format: csv\n"
        f"dest_table: orders\n"
        f"write_mode: append\n"
        f"retry_cap: 3\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n  type: sqlite\n  url: {dest_db_url}\n"
        f"columns:\n"
        f"  - source: order_id\n    dest: order_id\n    type: string\n    required: true\n"
        f"  - source: amount\n    dest: amount\n    type: float\n    required: true\n"
    )
    audit_db_url = f"sqlite:///{tmp_path}/filedge.db"
    dest_db_path = str(tmp_path / "orders.db")
    return str(watched), str(config_file), audit_db_url, dest_db_path


def _force_processing_stale(audit_db_url: str) -> None:
    """Back-date all COMMITTED records to PROCESSING to simulate a crashed worker."""
    stale_ts = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    ).isoformat()
    path = audit_db_url.removeprefix("sqlite:///")
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE etl_file_audit SET state='PROCESSING', claimed_at=? WHERE state='COMMITTED'",
        [stale_ts],
    )
    conn.commit()
    conn.close()


def _dest_row_count(dest_db_path: str) -> int:
    return sqlite3.connect(dest_db_path).execute(
        "SELECT COUNT(*) FROM orders"
    ).fetchone()[0]


def test_crash_retry_reclaims_stale_lock(crash_retry_env):
    """Second run reports reclaimed=1 for the stale PROCESSING lock."""
    watched, config, audit_url, dest_path = crash_retry_env

    run_pipeline(watched, config, audit_url, preflight=False)
    _force_processing_stale(audit_url)
    result2 = run_pipeline(watched, config, audit_url, preflight=False)

    assert result2["reclaimed"] == 1


def test_crash_retry_no_row_duplication(crash_retry_env):
    """Destination row count after retry equals the original commit — no duplicates."""
    watched, config, audit_url, dest_path = crash_retry_env

    run_pipeline(watched, config, audit_url, preflight=False)
    rows_after_first = _dest_row_count(dest_path)

    _force_processing_stale(audit_url)
    run_pipeline(watched, config, audit_url, preflight=False)
    rows_after_retry = _dest_row_count(dest_path)

    assert rows_after_retry == rows_after_first


def test_crash_retry_status_is_clean(crash_retry_env):
    """After recovery filedge status shows 0 PROCESSING and 1 COMMITTED."""
    watched, config, audit_url, dest_path = crash_retry_env

    run_pipeline(watched, config, audit_url, preflight=False)
    _force_processing_stale(audit_url)
    run_pipeline(watched, config, audit_url, preflight=False)

    db = Database(audit_url)
    summary = get_status_summary(db)
    db.close()

    assert summary["PROCESSING"] == 0
    assert summary["COMMITTED"] == 1


def test_crash_retry_committed_file_is_same_content_hash(crash_retry_env):
    """The COMMITTED record after retry is for the same content hash as the original file."""
    import sqlite3 as _sqlite3
    from filedge.hashing import compute_hash
    from filedge.filesystem import get_filesystem

    watched, config, audit_url, dest_path = crash_retry_env

    run_pipeline(watched, config, audit_url, preflight=False)
    _force_processing_stale(audit_url)
    run_pipeline(watched, config, audit_url, preflight=False)

    import os
    file_path = os.path.join(watched, "orders.csv")
    fs, _ = get_filesystem(watched)
    expected_hash = compute_hash(file_path, fs)

    audit_path = audit_url.removeprefix("sqlite:///")
    row = _sqlite3.connect(audit_path).execute(
        "SELECT content_hash, state FROM etl_file_audit WHERE state='COMMITTED'"
    ).fetchone()

    assert row is not None
    assert row[0] == expected_hash
    assert row[1] == "COMMITTED"
