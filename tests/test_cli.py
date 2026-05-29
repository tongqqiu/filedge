import json
import importlib.util

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.db import (
    Database,
    claim_processing,
    create_audit_tables,
    find_file_by_hash,
    insert_pending,
    mark_committed,
    mark_failed,
)


def test_author_command_reports_missing_optional_extra(tmp_path):
    if importlib.util.find_spec("textual") is not None:
        pytest.skip("textual is installed in this environment")
    sample = tmp_path / "sample.csv"
    sample.write_text("id\n1\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["author", str(sample)])

    assert result.exit_code == 1
    assert "pip install filedge[authoring]" in result.output


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path}/cli_test.db"
    db = Database(url)
    create_audit_tables(db)
    db.close()
    return url


def _write_minimal_pipeline_config(path, dest_db_url):
    path.write_text(
        f"format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n  type: sqlite\n  url: {dest_db_url}\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
    )


def test_run_json_writes_summary_to_stdout(tmp_path, db_url):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name\nAlice\n")
    config_path = tmp_path / "pipeline.yaml"
    _write_minimal_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "run",
        "--dir", str(watched),
        "--config", str(config_path),
        "--audit-db-url", db_url,
        "--no-progress",
        "--json",
    ])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["committed"] == 1
    assert summary["failed"] == 0
    assert "run_id" in summary
    assert "duration_s" in summary


def test_run_exits_nonzero_when_a_file_fails(tmp_path, db_url):
    watched = tmp_path / "watch"
    watched.mkdir()
    # Missing required column 'name' — file will fail to load.
    (watched / "bad.csv").write_text("other\nvalue\n")
    config_path = tmp_path / "pipeline.yaml"
    _write_minimal_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "run",
        "--dir", str(watched),
        "--config", str(config_path),
        "--audit-db-url", db_url,
        "--no-progress",
    ])

    assert result.exit_code == 1, result.output


def test_run_log_level_warning_suppresses_info_progress_lines(tmp_path, db_url):
    """`filedge run --log-level WARNING` must hide INFO-level progress log lines
    while still emitting WARNING+ (e.g. file load errors)."""
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name\nAlice\n")
    config_path = tmp_path / "pipeline.yaml"
    _write_minimal_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "run",
        "--dir", str(watched),
        "--config", str(config_path),
        "--audit-db-url", db_url,
        "--no-progress",
        "--log-format", "json",
        "--log-level", "WARNING",
    ])

    assert result.exit_code == 0, result.output
    # No INFO-level pipeline lines should appear (Click captures stderr in result.stderr).
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    info_lines = [line for line in stderr_lines if '"level": "INFO"' in line]
    assert info_lines == [], f"expected zero INFO lines, got: {info_lines}"


def test_status_default_output(db_url):
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--audit-db-url", db_url])
    assert result.exit_code == 0
    assert "COMMITTED:" in result.output
    assert "FAILED:" in result.output


def test_status_json_output_shape(db_url):
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--audit-db-url", db_url, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "PENDING" in data
    assert "PROCESSING" in data
    assert "COMMITTED" in data
    assert "FAILED" in data
    assert "recent_failures" in data
    assert isinstance(data["recent_failures"], list)


def test_status_json_counts_are_numbers(db_url):
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--audit-db-url", db_url, "--json"])
    data = json.loads(result.output)
    assert isinstance(data["COMMITTED"], int)


def test_status_default_shows_recent_failures(db_url):
    db = Database(db_url)
    insert_pending(db, "broken.csv", "brokenhash")
    claim_processing(db, "brokenhash")
    mark_failed(db, "brokenhash", "missing column 'amount'")
    db.commit()
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--audit-db-url", db_url])
    assert "broken.csv" in result.output
    assert "missing column" in result.output


def test_status_json_includes_failure_details(db_url):
    db = Database(db_url)
    insert_pending(db, "broken.csv", "brokenhash2")
    claim_processing(db, "brokenhash2")
    mark_failed(db, "brokenhash2", "bad type on row 5")
    db.commit()
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--audit-db-url", db_url, "--json"])
    data = json.loads(result.output)
    assert data["FAILED"] == 1
    assert data["recent_failures"][0]["filename"] == "broken.csv"
    assert "bad type" in data["recent_failures"][0]["error_message"]


def _write_run_config(path, dest_db_url):
    path.write_text(
        f"format: csv\n"
        f"dest_table: items\n"
        f"retry_cap: 3\n"
        f"batch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n"
        f"  type: sqlite\n"
        f"  url: {dest_db_url}\n"
        f"columns:\n"
        f"  - source: name\n"
        f"    dest: name\n"
        f"    type: string\n"
        f"    required: true\n"
        f"  - source: value\n"
        f"    dest: value\n"
        f"    type: string\n"
        f"    required: true\n"
    )


def test_run_accepts_no_progress_flag(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text("name,value\nAlice,1\n")
    config_file = tmp_path / "pipeline.yaml"
    _write_run_config(config_file, f"sqlite:///{tmp_path}/dest.db")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--dir",
            str(watched),
            "--config",
            str(config_file),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--no-progress",
        ],
    )

    assert result.exit_code == 0
    assert "Committed: 1" in result.output


# --- requeue helpers ---

def _make_terminal_failed(db, filename, content_hash, retry_cap=3):
    insert_pending(db, filename, content_hash)
    for _ in range(retry_cap):
        claim_processing(db, content_hash)
        mark_failed(db, content_hash, "persistent error")
    db.commit()


# --- requeue tests ---

def test_requeue_single_file_by_filename(db_url):
    db = Database(db_url)
    _make_terminal_failed(db, "orders.csv", "hash1")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["requeue", "orders.csv", "--audit-db-url", db_url])
    assert result.exit_code == 0
    assert "Requeued" in result.output

    db = Database(db_url)
    record = find_file_by_hash(db, "hash1")
    db.close()
    assert record.state == "PENDING"
    assert record.attempt_count == 0
    assert record.error_message is None


def test_requeue_single_file_by_hash(db_url):
    db = Database(db_url)
    _make_terminal_failed(db, "orders.csv", "hash2")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli, ["requeue", "orders.csv", "--hash", "hash2", "--audit-db-url", db_url]
    )
    assert result.exit_code == 0
    assert "Requeued" in result.output

    db = Database(db_url)
    assert find_file_by_hash(db, "hash2").state == "PENDING"
    db.close()


def test_requeue_single_file_not_found_errors(db_url):
    runner = CliRunner()
    result = runner.invoke(cli, ["requeue", "missing.csv", "--audit-db-url", db_url])
    assert result.exit_code == 1
    assert "no terminal-FAILED record" in result.output


def test_requeue_ambiguous_filename_errors_and_lists_candidates(db_url):
    db = Database(db_url)
    _make_terminal_failed(db, "orders.csv", "hashA")
    _make_terminal_failed(db, "orders.csv", "hashB")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["requeue", "orders.csv", "--audit-db-url", db_url])
    assert result.exit_code == 1
    assert "disambiguate" in result.output
    assert "hashA" in result.output
    assert "hashB" in result.output


def test_requeue_hash_not_terminal_failed_errors(db_url):
    db = Database(db_url)
    insert_pending(db, "orders.csv", "committed_hash")
    claim_processing(db, "committed_hash")
    mark_committed(db, "committed_hash")
    db.commit()
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli, ["requeue", "orders.csv", "--hash", "committed_hash", "--audit-db-url", db_url]
    )
    assert result.exit_code == 1
    assert "not eligible" in result.output


def test_requeue_all_terminal_failed_no_yes_shows_count(db_url):
    db = Database(db_url)
    _make_terminal_failed(db, "a.csv", "ha")
    _make_terminal_failed(db, "b.csv", "hb")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["requeue", "--all-terminal-failed", "--audit-db-url", db_url])
    assert result.exit_code == 1
    assert "2" in result.output
    assert "--yes" in result.output

    db = Database(db_url)
    assert find_file_by_hash(db, "ha").state == "FAILED"  # unchanged
    db.close()


def test_requeue_all_terminal_failed_dry_run_lists_files(db_url):
    db = Database(db_url)
    _make_terminal_failed(db, "a.csv", "ha2")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli, ["requeue", "--all-terminal-failed", "--dry-run", "--audit-db-url", db_url]
    )
    assert result.exit_code == 0
    assert "a.csv" in result.output
    assert "ha2" in result.output
    assert "Would requeue" in result.output

    db = Database(db_url)
    assert find_file_by_hash(db, "ha2").state == "FAILED"  # unchanged
    db.close()


def test_requeue_all_terminal_failed_yes_resets_all(db_url):
    db = Database(db_url)
    _make_terminal_failed(db, "a.csv", "ha3")
    _make_terminal_failed(db, "b.csv", "hb3")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli, ["requeue", "--all-terminal-failed", "--yes", "--audit-db-url", db_url]
    )
    assert result.exit_code == 0
    assert "Requeued: 2" in result.output

    db = Database(db_url)
    assert find_file_by_hash(db, "ha3").state == "PENDING"
    assert find_file_by_hash(db, "hb3").state == "PENDING"
    db.close()


def test_requeue_all_terminal_failed_none_found(db_url):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["requeue", "--all-terminal-failed", "--audit-db-url", db_url]
    )
    assert result.exit_code == 0
    assert "No terminal-FAILED" in result.output


def test_requeue_mutual_exclusion_filename_and_all(db_url):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["requeue", "orders.csv", "--all-terminal-failed", "--audit-db-url", db_url]
    )
    assert result.exit_code == 1
    assert "not both" in result.output


def test_requeue_dry_run_and_yes_mutually_exclusive(db_url):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["requeue", "--all-terminal-failed", "--dry-run", "--yes", "--audit-db-url", db_url],
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_status_json_includes_content_hash_in_failures(db_url):
    db = Database(db_url)
    insert_pending(db, "broken.csv", "brokenhash3")
    claim_processing(db, "brokenhash3")
    mark_failed(db, "brokenhash3", "type error")
    db.commit()
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--audit-db-url", db_url, "--json"])
    data = json.loads(result.output)
    assert data["recent_failures"][0]["content_hash"] == "brokenhash3"


def test_run_accepts_progress_flag(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text("name,value\nAlice,1\n")
    config_file = tmp_path / "pipeline.yaml"
    _write_run_config(config_file, f"sqlite:///{tmp_path}/dest.db")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--dir",
            str(watched),
            "--config",
            str(config_file),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--progress",
        ],
    )

    assert result.exit_code == 0
    assert "Committed: 1" in result.output


def test_export_audit_creates_html_file(db_url, tmp_path):
    runner = CliRunner()
    output = tmp_path / "site" / "index.html"
    result = runner.invoke(cli, [
        "export-audit",
        "--audit-db-url", db_url,
        "--output", str(output),
    ])
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_lineage_prints_source_metadata_for_known_hash(tmp_path, db_url):
    from filedge.source_manifest import SourceMetadata
    db = Database(db_url)
    insert_pending(
        db, "stripe.ndjson", "h-stripe",
        source_metadata=SourceMetadata(
            source_type="api",
            source_name="stripe.charges",
            producer="https://github.com/dlt-hub/dlt",
            external_run_id="dlt-run-xyz",
            raw_payload="{}",
        ),
    )
    claim_processing(db, "h-stripe", run_id="filedge-run-1")
    mark_committed(db, "h-stripe", row_count=42)
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "h-stripe", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    assert "h-stripe" in result.output
    assert "stripe.ndjson" in result.output
    assert "COMMITTED" in result.output
    assert "api" in result.output
    assert "stripe.charges" in result.output
    assert "dlt-hub/dlt" in result.output
    assert "dlt-run-xyz" in result.output
    assert "filedge-run-1" in result.output


def test_lineage_for_file_without_source_metadata_still_works(tmp_path, db_url):
    db = Database(db_url)
    insert_pending(db, "direct.csv", "h-direct")
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "h-direct", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    assert "direct.csv" in result.output
    assert "PENDING" in result.output


def test_lineage_unknown_hash_exits_nonzero(tmp_path, db_url):
    result = CliRunner().invoke(cli, ["lineage", "nope-not-here", "--audit-db-url", db_url])
    assert result.exit_code != 0


def test_lineage_shows_source_range_and_timestamps_when_present(tmp_path, db_url):
    from filedge.source_manifest import SourceMetadata
    db = Database(db_url)
    insert_pending(
        db, "kafka.ndjson", "h-kafka",
        source_metadata=SourceMetadata(
            source_type="queue", source_name="kafka.orders",
            producer="https://github.com/apache/kafka-connect",
            external_run_id="kc-run-1",
            raw_payload="{}",
            manifest_version="1",
            started_at="2026-05-24T10:00:00Z",
            finished_at="2026-05-24T10:30:00Z",
            record_count=1500,
            source_range={"topic": "orders", "partition": 3, "start_offset": 1000, "end_offset": 2000},
        ),
    )
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "h-kafka", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    assert "manifest_version" in result.output and "1" in result.output
    assert "started_at" in result.output and "2026-05-24T10:00:00Z" in result.output
    assert "finished_at" in result.output and "2026-05-24T10:30:00Z" in result.output
    assert "record_count" in result.output and "1500" in result.output
    assert "source_range" in result.output
    assert "orders" in result.output


def test_lineage_by_filename_returns_record_when_unique(tmp_path, db_url):
    db = Database(db_url)
    insert_pending(db, "stripe.ndjson", "h-stripe-1")
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "stripe.ndjson", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    assert "h-stripe-1" in result.output
    assert "stripe.ndjson" in result.output


def test_lineage_by_filename_with_multiple_hashes_disambiguates(tmp_path, db_url):
    db = Database(db_url)
    insert_pending(db, "shared.ndjson", "h-shared-1")
    insert_pending(db, "shared.ndjson", "h-shared-2")
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "shared.ndjson", "--audit-db-url", db_url])
    assert result.exit_code != 0, result.output
    assert "h-shared-1" in result.output
    assert "h-shared-2" in result.output
    assert "re-run with one of these Content Hashes" in result.output


def test_lineage_json_output(tmp_path, db_url):
    from filedge.source_manifest import SourceMetadata
    db = Database(db_url)
    insert_pending(
        db, "stripe.ndjson", "h-stripe-j",
        source_metadata=SourceMetadata(
            source_type="api", source_name="stripe.charges",
            producer="https://github.com/dlt-hub/dlt",
            external_run_id="dlt-run-j",
            raw_payload='{"foo":1}',
            manifest_version="1",
            started_at="2026-05-24T10:00:00Z",
            finished_at="2026-05-24T10:30:00Z",
            record_count=42,
            source_range={"cursor_start": "a", "cursor_end": "b"},
        ),
    )
    claim_processing(db, "h-stripe-j", run_id="filedge-run-j")
    mark_committed(db, "h-stripe-j", row_count=42)
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "h-stripe-j", "--json", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filename"] == "stripe.ndjson"
    assert payload["content_hash"] == "h-stripe-j"
    assert payload["state"] == "COMMITTED"
    assert payload["row_count"] == 42
    assert payload["run_id"] == "filedge-run-j"
    assert payload["source_manifest"]["source_type"] == "api"
    assert payload["source_manifest"]["source_name"] == "stripe.charges"
    assert payload["source_manifest"]["source_range"] == {"cursor_start": "a", "cursor_end": "b"}


def test_lineage_json_for_file_without_source_metadata(tmp_path, db_url):
    db = Database(db_url)
    insert_pending(db, "direct.csv", "h-direct-j")
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["lineage", "h-direct-j", "--json", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filename"] == "direct.csv"
    assert payload["source_manifest"] is None


def test_lineage_dest_table_flag_appears_in_output(tmp_path, db_url):
    db = Database(db_url)
    insert_pending(db, "a.csv", "h-dt")
    db.commit()
    db.close()

    result = CliRunner().invoke(
        cli,
        ["lineage", "h-dt", "--audit-db-url", db_url, "--dest-table", "items"],
    )
    assert result.exit_code == 0, result.output
    assert "dest_table" in result.output
    assert "items" in result.output


def test_lineage_dest_table_in_json(tmp_path, db_url):
    db = Database(db_url)
    insert_pending(db, "a.csv", "h-dt-j")
    db.commit()
    db.close()

    result = CliRunner().invoke(
        cli,
        ["lineage", "h-dt-j", "--json", "--audit-db-url", db_url, "--dest-table", "items"],
    )
    payload = json.loads(result.output)
    assert payload["dest_table"] == "items"


def test_status_json_includes_source_metadata_for_recent_failures(tmp_path, db_url):
    """Recent failures with source metadata expose source_type/source_name/producer/external_run_id."""
    from filedge.source_manifest import SourceMetadata
    db = Database(db_url)
    # File with source metadata that fails
    insert_pending(
        db, "stripe.ndjson", "h-fail-stripe",
        source_metadata=SourceMetadata(
            source_type="api", source_name="stripe.charges",
            producer="https://github.com/dlt-hub/dlt",
            external_run_id="dlt-run-1",
            raw_payload="{}",
        ),
    )
    claim_processing(db, "h-fail-stripe")
    mark_failed(db, "h-fail-stripe", "boom")
    # File without source metadata that also fails
    insert_pending(db, "direct.csv", "h-fail-direct")
    claim_processing(db, "h-fail-direct")
    mark_failed(db, "h-fail-direct", "bad-row")
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["status", "--json", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    by_hash = {f["content_hash"]: f for f in payload["recent_failures"]}
    stripe = by_hash["h-fail-stripe"]
    assert stripe["source_type"] == "api"
    assert stripe["source_name"] == "stripe.charges"
    assert stripe["producer"] == "https://github.com/dlt-hub/dlt"
    assert stripe["external_run_id"] == "dlt-run-1"

    direct = by_hash["h-fail-direct"]
    assert direct["source_type"] is None
    assert direct["source_name"] is None
    assert direct["producer"] is None
    assert direct["external_run_id"] is None


def test_status_human_output_unchanged_when_failures_have_source_metadata(tmp_path, db_url):
    """Human `filedge status` must not gain source-metadata columns."""
    from filedge.source_manifest import SourceMetadata
    db = Database(db_url)
    insert_pending(
        db, "stripe.ndjson", "h-fail-stripe",
        source_metadata=SourceMetadata(
            source_type="api", source_name="stripe.charges",
            producer="x", external_run_id="y", raw_payload="{}",
        ),
    )
    claim_processing(db, "h-fail-stripe")
    mark_failed(db, "h-fail-stripe", "boom")
    db.commit()
    db.close()

    result = CliRunner().invoke(cli, ["status", "--audit-db-url", db_url])
    assert result.exit_code == 0, result.output
    # Human output should still be the lean failure summary
    assert "stripe.ndjson" in result.output
    assert "boom" in result.output
    assert "source_type" not in result.output
    assert "producer" not in result.output


def _make_terminal_failed_with_metadata(db, filename, content_hash, retry_cap=3):
    from filedge.source_manifest import SourceMetadata
    insert_pending(
        db, filename, content_hash,
        source_metadata=SourceMetadata(
            source_type="api", source_name="stripe.charges",
            producer="https://github.com/dlt-hub/dlt",
            external_run_id="dlt-run-q",
            raw_payload='{"foo":1}',
            manifest_version="1",
            started_at="2026-05-24T10:00:00Z",
            finished_at="2026-05-24T10:30:00Z",
            record_count=7,
            source_range={"cursor_start": "a", "cursor_end": "b"},
        ),
    )
    for _ in range(retry_cap):
        claim_processing(db, content_hash)
        mark_failed(db, content_hash, "persistent error")
    db.commit()


def test_requeue_by_hash_preserves_source_metadata_cli(db_url):
    db = Database(db_url)
    _make_terminal_failed_with_metadata(db, "stripe.ndjson", "h-cli-req")
    db.close()

    result = CliRunner().invoke(
        cli, ["requeue", "stripe.ndjson", "--hash", "h-cli-req", "--audit-db-url", db_url]
    )
    assert result.exit_code == 0, result.output

    db = Database(db_url)
    record = find_file_by_hash(db, "h-cli-req")
    db.close()
    assert record.state == "PENDING"
    assert record.attempt_count == 0
    assert record.source_type == "api"
    assert record.source_name == "stripe.charges"
    assert record.producer == "https://github.com/dlt-hub/dlt"
    assert record.external_run_id == "dlt-run-q"
    assert record.manifest_version == "1"
    assert record.started_at == "2026-05-24T10:00:00Z"
    assert record.finished_at == "2026-05-24T10:30:00Z"
    assert record.record_count == 7
    assert record.source_range == {"cursor_start": "a", "cursor_end": "b"}
    assert record.manifest_payload == '{"foo":1}'


def test_requeue_by_filename_preserves_source_metadata_cli(db_url):
    db = Database(db_url)
    _make_terminal_failed_with_metadata(db, "stripe.ndjson", "h-cli-req-fn")
    db.close()

    result = CliRunner().invoke(
        cli, ["requeue", "stripe.ndjson", "--audit-db-url", db_url]
    )
    assert result.exit_code == 0, result.output

    db = Database(db_url)
    record = find_file_by_hash(db, "h-cli-req-fn")
    db.close()
    assert record.state == "PENDING"
    assert record.source_type == "api"
    assert record.manifest_payload == '{"foo":1}'


def test_author_pipeline_flag_opens_existing_folder(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")

    # Author a Folder from scratch so there is something to re-open.
    from filedge.authoring_workflow import AuthoringWorkflow

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sample = tmp_path / "people.csv"
    sample.write_text("id,name\n1,Alice\n")
    wf = AuthoringWorkflow.start(
        file=str(sample), workspace=str(workspace), dest_table="people"
    )
    wf.validate()
    for review in wf.confidence_reviews():
        wf.acknowledge_confidence_tier(review.source)
    wf.generate()

    # Capture the workflow the TUI would receive, without launching the TUI.
    captured = {}

    class FakeApp:
        def __init__(self, workflow):
            captured["workflow"] = workflow

        def run(self):
            captured["ran"] = True

    import filedge.authoring_ui as ui

    monkeypatch.setattr(ui, "AuthoringApp", FakeApp)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["author", "--pipeline", "pipelines/people", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert captured["ran"] is True
    assert captured["workflow"].reauthor is True
    assert {c.source for c in captured["workflow"].draft.columns} == {"id", "name"}


def test_author_rejects_both_sample_and_pipeline(tmp_path):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    sample = tmp_path / "s.csv"
    sample.write_text("id\n1\n")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["author", str(sample), "--pipeline", "pipelines/x"]
    )
    assert result.exit_code == 2
    assert "not both" in result.output


def test_author_requires_sample_or_pipeline(tmp_path):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["author", "--workspace", str(workspace)])
    assert result.exit_code == 2
    assert "SAMPLE_FILE or --pipeline" in result.output


def test_author_no_args_launches_browse_when_registry_exists(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    from filedge.authoring_workflow import AuthoringWorkflow

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sample = tmp_path / "people.csv"
    sample.write_text("id,name\n1,Alice\n")
    wf = AuthoringWorkflow.start(
        file=str(sample), workspace=str(workspace), dest_table="people"
    )
    wf.validate()
    for review in wf.confidence_reviews():
        wf.acknowledge_confidence_tier(review.source)
    wf.generate()

    captured = {}

    class FakeBrowse:
        def __init__(self, entries):
            captured["entries"] = entries
            self.selected_folder = "pipelines/people"

        def run(self):
            captured["browse_ran"] = True

    class FakeApp:
        def __init__(self, workflow):
            captured["workflow"] = workflow

        def run(self):
            captured["ran"] = True

    import filedge.authoring_browse as browse_mod
    import filedge.authoring_ui as ui

    monkeypatch.setattr(browse_mod, "PipelineBrowseApp", FakeBrowse)
    monkeypatch.setattr(ui, "AuthoringApp", FakeApp)

    runner = CliRunner()
    result = runner.invoke(cli, ["author", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert captured["browse_ran"] is True
    assert captured["ran"] is True
    assert captured["workflow"].reauthor is True
    assert {e.folder for e in captured["entries"]} == {"pipelines/people"}


def test_author_no_args_browse_quit_exits_cleanly(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    from filedge.authoring_workflow import AuthoringWorkflow

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sample = tmp_path / "people.csv"
    sample.write_text("id,name\n1,Alice\n")
    wf = AuthoringWorkflow.start(
        file=str(sample), workspace=str(workspace), dest_table="people"
    )
    wf.validate()
    for review in wf.confidence_reviews():
        wf.acknowledge_confidence_tier(review.source)
    wf.generate()

    class FakeBrowse:
        def __init__(self, entries):
            self.selected_folder = None  # user quit without choosing

        def run(self):
            pass

    import filedge.authoring_browse as browse_mod

    monkeypatch.setattr(browse_mod, "PipelineBrowseApp", FakeBrowse)

    runner = CliRunner()
    result = runner.invoke(cli, ["author", "--workspace", str(workspace)])
    assert result.exit_code == 0


def test_author_no_args_browse_summary_failure_is_reported(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Write a malformed Registry so list_browse_entries raises.
    (workspace / "pipeline-registry.yaml").write_text("version: 1\npipelines: not-a-list\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["author", "--workspace", str(workspace)])
    assert result.exit_code == 2
    assert "Error:" in result.output


def test_author_no_args_new_pipeline_choice_asks_for_sample(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    from filedge.authoring_browse import NEW_PIPELINE_SENTINEL
    from filedge.authoring_workflow import AuthoringWorkflow

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sample = tmp_path / "people.csv"
    sample.write_text("id,name\n1,Alice\n")
    wf = AuthoringWorkflow.start(
        file=str(sample), workspace=str(workspace), dest_table="people"
    )
    wf.validate()
    for review in wf.confidence_reviews():
        wf.acknowledge_confidence_tier(review.source)
    wf.generate()

    class FakeBrowse:
        def __init__(self, entries):
            self.selected_folder = NEW_PIPELINE_SENTINEL

        def run(self):
            pass

    import filedge.authoring_browse as browse_mod

    monkeypatch.setattr(browse_mod, "PipelineBrowseApp", FakeBrowse)

    runner = CliRunner()
    result = runner.invoke(cli, ["author", "--workspace", str(workspace)])
    assert result.exit_code == 2
    assert "SAMPLE_FILE" in result.output


def test_author_from_scratch_launches_tui(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sample = tmp_path / "people.csv"
    sample.write_text("id,name\n1,Alice\n")

    captured = {}

    class FakeApp:
        def __init__(self, workflow):
            captured["workflow"] = workflow

        def run(self):
            captured["ran"] = True

    import filedge.authoring_ui as ui

    monkeypatch.setattr(ui, "AuthoringApp", FakeApp)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["author", str(sample), "--workspace", str(workspace)]
    )

    assert result.exit_code == 0, result.output
    assert captured["ran"] is True
    # dest_table defaults to the sample File stem; from-scratch is not re-author.
    assert captured["workflow"].dest_table == "people"
    assert captured["workflow"].reauthor is False


def test_author_pipeline_reports_clear_error_for_unknown_folder(tmp_path, monkeypatch):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Never launch a TUI even if construction unexpectedly succeeds.
    import filedge.authoring_ui as ui

    monkeypatch.setattr(ui, "AuthoringApp", lambda wf: type("A", (), {"run": lambda s: None})())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["author", "--pipeline", "pipelines/missing", "--workspace", str(workspace)],
    )

    assert result.exit_code == 2
    assert "Error:" in result.output


def test_author_rejects_sheet_without_excel_format(tmp_path):
    if importlib.util.find_spec("textual") is None:
        pytest.skip("textual extra not installed")
    sample = tmp_path / "s.csv"
    sample.write_text("id\n1\n")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["author", str(sample), "--format", "csv", "--sheet", "Orders"]
    )
    assert result.exit_code == 2
    assert "--sheet is only valid with --format excel" in result.output
