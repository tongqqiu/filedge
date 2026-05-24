import json
import sqlite3

from click.testing import CliRunner

from filedge.cli import cli


def _write_pipeline_config(path, dest_db_url):
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
    )


def _table_exists(db_path, table):
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            [table],
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def test_healthcheck_json_reports_both_healthy_for_sqlite(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["healthy"] is True
    assert [check["name"] for check in payload["checks"]] == [
        "audit_db",
        "destination",
    ]
    assert all(check["ok"] for check in payload["checks"])


def test_healthcheck_json_reports_audit_db_unreachable(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/missing/audit.db",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    audit = payload["checks"][0]
    assert audit["name"] == "audit_db"
    assert audit["ok"] is False
    assert audit["error"]
    assert payload["checks"][1]["ok"] is True


def test_healthcheck_json_reports_destination_unreachable(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/missing/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    assert payload["checks"][0]["ok"] is True
    destination = payload["checks"][1]
    assert destination["name"] == "destination"
    assert destination["ok"] is False
    assert destination["error"]


def test_healthcheck_json_reports_both_failing(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/missing-dest/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/missing-audit/audit.db",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    assert [check["ok"] for check in payload["checks"]] == [False, False]


def test_run_preflight_failure_exits_before_tables_or_rows(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text("name\nAlice\n")
    audit_db = tmp_path / "audit.db"
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/missing-dest/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--dir",
            str(watched),
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{audit_db}",
            "--no-progress",
        ],
    )

    assert result.exit_code == 1
    assert "Healthcheck failed: destination unreachable:" in result.stderr
    assert not _table_exists(audit_db, "etl_file_audit")
    assert not _table_exists(tmp_path / "missing-dest" / "dest.db", "items")


def test_healthcheck_text_reports_healthy_checks(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "audit_db: ok" in result.stdout
    assert "destination: ok" in result.stdout


def test_healthcheck_text_reports_unhealthy_checks_to_stderr(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/missing/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
        ],
    )

    assert result.exit_code == 1
    assert "audit_db: ok" in result.stdout
    assert "destination: unreachable:" in result.stderr


def test_healthcheck_invalid_config_reports_configuration_error(tmp_path):
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text("format: [")

    result = CliRunner().invoke(
        cli,
        [
            "healthcheck",
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
        ],
    )

    assert result.exit_code == 1
    assert "Healthcheck failed: configuration unreachable:" in result.stderr


def test_run_defaults_progress_from_stderr_tty(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text("name\nAlice\n")
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--dir",
            str(watched),
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["committed"] == 1


def test_run_schema_error_is_reported_without_generic_prefix(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text("name\nAlice\n")
    dest_db = tmp_path / "dest.db"
    conn = sqlite3.connect(dest_db)
    conn.execute("CREATE TABLE items (name INTEGER)")
    conn.commit()
    conn.close()
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{dest_db}")

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--dir",
            str(watched),
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--no-progress",
        ],
    )

    assert result.exit_code == 1
    assert "Schema error:" in result.stderr
    assert "Error: Schema error:" not in result.stderr


def test_run_unexpected_error_uses_generic_error_prefix(tmp_path, monkeypatch):
    watched = tmp_path / "watch"
    watched.mkdir()
    config_path = tmp_path / "pipeline.yaml"
    _write_pipeline_config(config_path, f"sqlite:///{tmp_path}/dest.db")

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("filedge.cli.run_pipeline", boom)

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--dir",
            str(watched),
            "--config",
            str(config_path),
            "--audit-db-url",
            f"sqlite:///{tmp_path}/audit.db",
            "--no-progress",
        ],
    )

    assert result.exit_code == 1
    assert "Error: boom" in result.stderr
