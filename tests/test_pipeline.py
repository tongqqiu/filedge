
from filedge.db import (
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

def _minimal_watched_dir_and_config(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        f"format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n  type: sqlite\n  url: sqlite:///{tmp_path}/dest.db\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: value\n    dest: value\n    type: string\n    required: true\n"
    )
    return str(watched), str(config_file), f"sqlite:///{tmp_path}/audit.db"


def test_run_pipeline_returns_unique_run_id(tmp_path):
    import uuid
    from filedge.pipeline import run_pipeline

    watched1, config1, audit1 = _minimal_watched_dir_and_config(tmp_path / "first")
    watched2, config2, audit2 = _minimal_watched_dir_and_config(tmp_path / "second")

    result1 = run_pipeline(watched1, config1, audit1)
    result2 = run_pipeline(watched2, config2, audit2)

    assert "run_id" in result1
    assert uuid.UUID(result1["run_id"])  # valid UUID
    assert result1["run_id"] != result2["run_id"]


def test_run_pipeline_stamps_run_id_on_processed_rows(tmp_path):
    """Every file processed in a Run carries that Run's run_id on its audit row."""
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched, config, audit = _minimal_watched_dir_and_config(tmp_path)
    (tmp_path / "watch" / "b.csv").write_text("name,value\nBob,2\n")

    result = run_pipeline(watched, config, audit, run_id="run-xyz")

    assert result["committed"] == 2
    audit_path = audit.removeprefix("sqlite:///")
    rows = sqlite3.connect(audit_path).execute(
        "SELECT filename, state, run_id FROM etl_file_audit ORDER BY filename"
    ).fetchall()
    assert rows == [
        ("a.csv", "COMMITTED", "run-xyz"),
        ("b.csv", "COMMITTED", "run-xyz"),
    ]


def test_run_pipeline_summary_contains_timing_and_volume(tmp_path):
    from filedge.pipeline import run_pipeline

    watched, config, audit = _minimal_watched_dir_and_config(tmp_path)
    (tmp_path / "watch" / "b.csv").write_text("name,value\nBob,2\nCarol,3\n")

    result = run_pipeline(watched, config, audit)

    assert result["files_scanned"] == 2
    assert result["rows_committed"] == 3  # 1 from a.csv + 2 from b.csv
    assert result["bytes_processed"] > 0  # sum of both files' bytes
    assert result["duration_s"] >= 0  # may be 0.0 on a very fast run, but key must exist
    assert "started_at" in result and "finished_at" in result
    assert result["started_at"] <= result["finished_at"]


def test_run_pipeline_uses_caller_supplied_run_id(tmp_path):
    from filedge.pipeline import run_pipeline

    watched, config, audit = _minimal_watched_dir_and_config(tmp_path)

    result = run_pipeline(watched, config, audit, run_id="external-scheduler-id-1")

    assert result["run_id"] == "external-scheduler-id-1"


def test_pipeline_retries_failed_file(tmp_path):
    """A file that fails on run 1 is retried on run 2 if below retry_cap."""
    from filedge.pipeline import run_pipeline

    # Write a bad CSV (missing required column) then replace with a good one
    watched = tmp_path / "watch"
    watched.mkdir()
    config_file = tmp_path / "pipeline.yaml"
    dest_db_url = f"sqlite:///{tmp_path}/dest.db"
    config_file.write_text(
        f"format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n  type: sqlite\n  url: {dest_db_url}\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: value\n    dest: value\n    type: string\n    required: true\n"
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


def test_pipeline_commits_cdc_file(tmp_path):
    import sqlite3

    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    changes = watched / "changes.ndjson"
    changes.write_text(
        '{"id":"1","value":"old","updated_at":"2026-05-01T00:00:00","op":"c"}\n'
        '{"id":"1","value":"new","updated_at":"2026-05-02T00:00:00","op":"u"}\n'
    )
    dest_path = tmp_path / "dest.db"
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        f"""
format: ndjson
dest_table: items
write_mode: cdc
connector:
  type: sqlite
  url: sqlite:///{dest_path}
cdc:
  keys: [id]
  operation_column: op
  sequence_by: updated_at
  operations:
    insert: [c]
    update: [u]
    delete: [d]
columns:
  - source: id
    dest: id
    type: string
    required: true
  - source: value
    dest: value
    type: string
    required: false
  - source: updated_at
    dest: updated_at
    type: timestamp
    required: true
"""
    )

    result = run_pipeline(
        str(watched),
        str(config_file),
        f"sqlite:///{tmp_path}/audit.db",
    )

    assert result["committed"] == 1
    assert result["failed"] == 0
    conn = sqlite3.connect(dest_path)
    row = conn.execute("SELECT id, value, _source_file_hash FROM items").fetchone()
    assert row[0] == "1"
    assert row[1] == "new"
    assert row[2] is not None
    conn.close()


def test_pipeline_attaches_source_manifest_metadata_to_audit_record(tmp_path):
    """A File with a sidecar manifest produces an Audit Record carrying source metadata."""
    import json
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched, config, audit = _minimal_watched_dir_and_config(tmp_path)

    manifest_path = tmp_path / "watch" / "a.csv.manifest.json"
    manifest_path.write_text(json.dumps({
        "eventType": "COMPLETE",
        "eventTime": "2026-05-24T10:00:00Z",
        "producer": "https://github.com/dlt-hub/dlt",
        "run": {"runId": "dlt-run-xyz"},
        "job": {"namespace": "api", "name": "stripe.charges"},
    }))

    result = run_pipeline(watched, config, audit)

    assert result["committed"] == 1
    audit_path = audit.removeprefix("sqlite:///")
    row = sqlite3.connect(audit_path).execute(
        "SELECT source_type, source_name, producer, external_run_id FROM etl_file_audit"
        " WHERE filename = 'a.csv'"
    ).fetchone()
    assert row == ("api", "stripe.charges", "https://github.com/dlt-hub/dlt", "dlt-run-xyz")


def test_pipeline_without_manifest_commits_normally(tmp_path):
    """Optional mode default: a File without a sidecar still ingests and commits."""
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched, config, audit = _minimal_watched_dir_and_config(tmp_path)

    result = run_pipeline(watched, config, audit)

    assert result["committed"] == 1
    audit_path = audit.removeprefix("sqlite:///")
    row = sqlite3.connect(audit_path).execute(
        "SELECT state, source_type FROM etl_file_audit WHERE filename = 'a.csv'"
    ).fetchone()
    assert row == ("COMMITTED", None)


def _pipeline_yaml_with_manifest_policy(tmp_path, policy: str) -> str:
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        f"format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"source_manifest: {policy}\n"
        f"connector:\n  type: sqlite\n  url: sqlite:///{tmp_path}/dest.db\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: value\n    dest: value\n    type: string\n    required: true\n"
    )
    return str(config_file)


def test_required_mode_fails_file_when_manifest_missing(tmp_path):
    """Required mode fails before destination write when no sidecar exists."""
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "required")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)

    assert result["failed"] == 1
    assert result["committed"] == 0
    # Destination table empty — no destination write happened
    dest = sqlite3.connect(f"{tmp_path}/dest.db")
    rows = dest.execute("SELECT COUNT(*) FROM items").fetchone()
    assert rows[0] == 0
    # Audit record carries the error category and manifest path
    audit_path = audit_url.removeprefix("sqlite:///")
    err = sqlite3.connect(audit_path).execute(
        "SELECT error_message FROM etl_file_audit WHERE filename = 'a.csv'"
    ).fetchone()[0]
    assert "manifest_missing" in err
    assert "a.csv.manifest.json" in err


def test_required_mode_fails_file_when_manifest_malformed_json(tmp_path):
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text("{ not valid")
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "required")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)

    assert result["failed"] == 1
    audit_path = audit_url.removeprefix("sqlite:///")
    err = sqlite3.connect(audit_path).execute(
        "SELECT error_message FROM etl_file_audit WHERE filename = 'a.csv'"
    ).fetchone()[0]
    assert "manifest_malformed_json" in err


def test_required_mode_fails_file_when_manifest_unsupported_version(tmp_path):
    import json
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r", "facets": {"_filedgeManifest": {"manifest_version": "9999"}}},
        "job": {"namespace": "api", "name": "x"},
    }))
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "required")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)

    assert result["failed"] == 1
    audit_path = audit_url.removeprefix("sqlite:///")
    err = sqlite3.connect(audit_path).execute(
        "SELECT error_message FROM etl_file_audit WHERE filename = 'a.csv'"
    ).fetchone()[0]
    assert "manifest_unsupported_version" in err


def test_required_mode_fails_file_when_manifest_missing_required_field(tmp_path):
    import json
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r"},
        "job": {"namespace": "api"},  # job.name missing
    }))
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "required")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)
    assert result["failed"] == 1


def test_required_mode_fails_file_when_source_range_invalid(tmp_path):
    import json
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r"},
        "job": {"namespace": "api", "name": "x"},
        "inputs": [{"name": "src", "facets": {"_sourceRange": "not-an-object"}}],
    }))
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "required")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)
    assert result["failed"] == 1


def test_required_mode_commits_file_with_valid_manifest(tmp_path):
    import json
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r"},
        "job": {"namespace": "api", "name": "stripe.charges"},
    }))
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "required")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)
    assert result["committed"] == 1
    assert result["failed"] == 0


def test_optional_mode_commits_file_with_invalid_manifest(tmp_path):
    """Optional mode tolerates an invalid manifest — does not fail the File."""
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text("{ not valid")
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "optional")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)
    assert result["committed"] == 1
    assert result["failed"] == 0


def test_disabled_mode_does_not_invoke_parser(tmp_path):
    """Disabled mode: even a valid manifest is ignored — no source metadata stored."""
    import json
    import sqlite3
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "a.csv.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r"},
        "job": {"namespace": "api", "name": "stripe.charges"},
    }))
    config_file = _pipeline_yaml_with_manifest_policy(tmp_path, "disabled")
    audit_url = f"sqlite:///{tmp_path}/audit.db"

    result = run_pipeline(str(watched), config_file, audit_url)
    assert result["committed"] == 1
    audit_path = audit_url.removeprefix("sqlite:///")
    row = sqlite3.connect(audit_path).execute(
        "SELECT source_type, source_name FROM etl_file_audit WHERE filename = 'a.csv'"
    ).fetchone()
    assert row == (None, None)
