"""End-to-end Dead-Letter Quarantine through `filedge run` + a SQLite Destination:
under-threshold commits good rows and writes the sidecar; over-threshold fails
wholesale; the quarantine-disabled path is unchanged Strict Mode.
"""

import json
import os

from click.testing import CliRunner

from filedge.cli import cli
from filedge.db import Database, create_audit_tables, find_file_by_hash
import hashlib


def _pipeline_yaml(tmp_path, dest_db, *, quarantine=None):
    block = ""
    if quarantine is not None:
        block = (
            "quarantine:\n"
            "  enabled: true\n"
            f"  dir: {tmp_path / 'quarantine'}\n"
            + quarantine
        )
    return (
        "format: csv\n"
        "dest_table: items\n"
        "connector:\n"
        "  type: sqlite\n"
        f"  url: sqlite:///{dest_db}\n"
        + block +
        "columns:\n"
        "  - source: id\n"
        "    dest: id\n"
        "    type: integer\n"
        "    required: true\n"
        "  - source: amount\n"
        "    dest: amount\n"
        "    type: float\n"
        "    required: true\n"
    )


def _run(tmp_path, csv_text, pipeline_text):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text(csv_text)
    config = tmp_path / "pipeline.yaml"
    config.write_text(pipeline_text)
    audit = f"sqlite:///{tmp_path}/audit.db"
    result = CliRunner().invoke(cli, [
        "run", "--dir", str(watched), "--config", str(config),
        "--audit-db-url", audit, "--no-progress",
    ])
    return result, audit, csv_text


def _dest_rows(dest_db):
    db = Database(f"sqlite:///{dest_db}")
    rows = db.execute("SELECT id, amount FROM items ORDER BY id").fetchall()
    db.close()
    return rows


def test_under_threshold_commits_good_rows_and_writes_sidecar(tmp_path):
    dest = tmp_path / "dest.db"
    csv = "id,amount\n1,1.5\n2,n/a\n3,3.5\n"  # 1 bad of 3
    pipeline = _pipeline_yaml(tmp_path, dest, quarantine="  max_invalid_fraction: 0.5\n")
    result, audit, _ = _run(tmp_path, csv, pipeline)

    assert result.exit_code == 0, result.output
    # Good rows landed; the bad row did not.
    assert _dest_rows(dest) == [(1, 1.5), (3, 3.5)]

    db = Database(audit)
    create_audit_tables(db)
    rec = find_file_by_hash(db, hashlib.sha256(csv.encode()).hexdigest())
    db.close()
    assert rec.state == "COMMITTED"
    assert rec.row_count == 2
    assert rec.quarantined_row_count == 1
    assert rec.quarantine_path and os.path.isfile(rec.quarantine_path)

    quarantined = [json.loads(line) for line in open(rec.quarantine_path).read().splitlines()]
    assert quarantined[0]["row_number"] == 2
    assert quarantined[0]["column"] == "amount"


def test_over_threshold_fails_wholesale_with_no_sidecar(tmp_path):
    dest = tmp_path / "dest.db"
    csv = "id,amount\n1,a\n2,b\n3,3.5\n"  # 2 bad of 3 = 67% > 50%
    pipeline = _pipeline_yaml(tmp_path, dest, quarantine="  max_invalid_fraction: 0.5\n")
    result, audit, _ = _run(tmp_path, csv, pipeline)

    assert result.exit_code != 0
    # Nothing committed to the Destination (table may not even exist / be empty).
    db = Database(f"sqlite:///{dest}")
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchall()
    if tables:
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    db.close()

    rec_db = Database(audit)
    create_audit_tables(rec_db)
    rec = find_file_by_hash(rec_db, hashlib.sha256(csv.encode()).hexdigest())
    rec_db.close()
    assert rec.state == "FAILED"
    # No sidecar left behind.
    qdir = tmp_path / "quarantine"
    assert not qdir.exists() or os.listdir(qdir) == []


def test_disabled_quarantine_is_unchanged_strict_mode(tmp_path):
    dest = tmp_path / "dest.db"
    csv = "id,amount\n1,1.5\n2,n/a\n"  # one bad row, no quarantine block
    pipeline = _pipeline_yaml(tmp_path, dest)  # no quarantine
    result, audit, _ = _run(tmp_path, csv, pipeline)

    assert result.exit_code != 0  # whole File fails
    db = Database(audit)
    create_audit_tables(db)
    rec = find_file_by_hash(db, hashlib.sha256(csv.encode()).hexdigest())
    db.close()
    assert rec.state == "FAILED"
    assert rec.quarantined_row_count in (0, None)


def test_clean_file_with_quarantine_enabled_commits_all_no_sidecar(tmp_path):
    dest = tmp_path / "dest.db"
    csv = "id,amount\n1,1.5\n2,2.5\n"  # no bad rows
    pipeline = _pipeline_yaml(tmp_path, dest, quarantine="  max_invalid_rows: 5\n")
    result, audit, _ = _run(tmp_path, csv, pipeline)

    assert result.exit_code == 0, result.output
    assert _dest_rows(dest) == [(1, 1.5), (2, 2.5)]
    db = Database(audit)
    create_audit_tables(db)
    rec = find_file_by_hash(db, hashlib.sha256(csv.encode()).hexdigest())
    db.close()
    assert rec.state == "COMMITTED"
    assert rec.quarantined_row_count == 0
    assert rec.quarantine_path is None
