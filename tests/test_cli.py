import json

import pytest
from click.testing import CliRunner

from etl.cli import cli
from etl.db import (
    Database,
    claim_processing,
    create_audit_tables,
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
    result = runner.invoke(cli, ["status", "--db-url", db_url])
    assert result.exit_code == 0
    assert "COMMITTED:" in result.output
    assert "FAILED:" in result.output


def test_status_json_output_shape(db_url):
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--db-url", db_url, "--json"])
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
    result = runner.invoke(cli, ["status", "--db-url", db_url, "--json"])
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
    result = runner.invoke(cli, ["status", "--db-url", db_url])
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
    result = runner.invoke(cli, ["status", "--db-url", db_url, "--json"])
    data = json.loads(result.output)
    assert data["FAILED"] == 1
    assert data["recent_failures"][0]["filename"] == "broken.csv"
    assert "bad type" in data["recent_failures"][0]["error_message"]
