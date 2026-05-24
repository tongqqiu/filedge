import json

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


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path}/cli_test.db"
    db = Database(url)
    create_audit_tables(db)
    db.close()
    return url


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
