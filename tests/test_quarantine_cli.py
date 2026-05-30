"""Quarantine is surfaced in the Operator CLI: the run summary, status, lineage,
and validate's dry-run — so a partial commit is never silent.
"""

import hashlib
import json

from click.testing import CliRunner

from filedge.cli import cli


def _pipeline(tmp_path, dest_db, quarantine_block):
    return (
        "format: csv\n"
        "dest_table: items\n"
        "connector:\n"
        "  type: sqlite\n"
        f"  url: sqlite:///{dest_db}\n"
        + quarantine_block +
        "columns:\n"
        "  - source: id\n    dest: id\n    type: integer\n    required: true\n"
        "  - source: amount\n    dest: amount\n    type: float\n    required: true\n"
    )


def _Q(tmp):
    return (
        "quarantine:\n  enabled: true\n"
        f"  dir: {tmp / 'quarantine'}\n  max_invalid_fraction: 0.5\n"
    )


def _run_quarantine(tmp_path):
    """Run a quarantine-enabled pipeline that quarantines exactly one row."""
    watched = tmp_path / "watch"
    watched.mkdir()
    csv = "id,amount\n1,1.5\n2,n/a\n3,3.5\n"  # 1 bad of 3, under 50%
    (watched / "data.csv").write_text(csv)
    config = tmp_path / "pipeline.yaml"
    config.write_text(_pipeline(tmp_path, tmp_path / "dest.db", _Q(tmp_path)))
    audit = f"sqlite:///{tmp_path}/audit.db"
    result = CliRunner().invoke(cli, [
        "run", "--dir", str(watched), "--config", str(config),
        "--audit-db-url", audit, "--no-progress",
    ])
    assert result.exit_code == 0, result.output
    return result, audit, hashlib.sha256(csv.encode()).hexdigest()


def test_run_summary_reports_quarantined_rows(tmp_path):
    result, _, _ = _run_quarantine(tmp_path)
    assert "Quarantined rows: 1" in result.output


def test_run_summary_json_includes_quarantined_rows(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    csv = "id,amount\n1,1.5\n2,n/a\n3,3.5\n"
    (watched / "data.csv").write_text(csv)
    config = tmp_path / "pipeline.yaml"
    config.write_text(_pipeline(tmp_path, tmp_path / "dest.db", _Q(tmp_path)))
    result = CliRunner().invoke(cli, [
        "run", "--dir", str(watched), "--config", str(config),
        "--audit-db-url", f"sqlite:///{tmp_path}/audit.db", "--no-progress", "--json",
    ])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["quarantined_rows"] == 1


def test_status_shows_quarantined_total(tmp_path):
    _, audit, _ = _run_quarantine(tmp_path)
    result = CliRunner().invoke(cli, ["status", "--audit-db-url", audit])
    assert result.exit_code == 0
    assert "QUARANTINED ROWS: 1" in result.output


def test_status_json_includes_quarantined_rows(tmp_path):
    _, audit, _ = _run_quarantine(tmp_path)
    result = CliRunner().invoke(cli, ["status", "--audit-db-url", audit, "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["quarantined_rows"] == 1


def test_lineage_shows_quarantine_count_and_sidecar(tmp_path):
    _, audit, content_hash = _run_quarantine(tmp_path)
    result = CliRunner().invoke(cli, ["lineage", content_hash, "--audit-db-url", audit])
    assert result.exit_code == 0, result.output
    assert "quarantined_rows: 1" in result.output
    assert "quarantine_file:" in result.output
    assert ".quarantine.ndjson" in result.output


def test_lineage_json_includes_quarantine_fields(tmp_path):
    _, audit, content_hash = _run_quarantine(tmp_path)
    result = CliRunner().invoke(
        cli, ["lineage", content_hash, "--audit-db-url", audit, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["quarantined_row_count"] == 1
    assert payload["quarantine_path"].endswith(".quarantine.ndjson")


def test_status_without_quarantine_omits_the_line(tmp_path):
    # A clean run (no quarantine config) does not print the quarantine line.
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "data.csv").write_text("id,amount\n1,1.5\n")
    config = tmp_path / "pipeline.yaml"
    config.write_text(_pipeline(tmp_path, tmp_path / "dest.db", ""))
    audit = f"sqlite:///{tmp_path}/audit.db"
    CliRunner().invoke(cli, [
        "run", "--dir", str(watched), "--config", str(config),
        "--audit-db-url", audit, "--no-progress",
    ])
    result = CliRunner().invoke(cli, ["status", "--audit-db-url", audit])
    assert "QUARANTINED ROWS" not in result.output


def test_validate_dry_run_reports_within_threshold(tmp_path):
    sample = tmp_path / "sample.csv"
    sample.write_text("id,amount\n1,1.5\n2,n/a\n3,3.5\n")  # 1 bad of 3, under 50%
    config = tmp_path / "pipeline.yaml"
    config.write_text(_pipeline(tmp_path, tmp_path / "dest.db", _Q(tmp_path)))
    result = CliRunner().invoke(cli, ["validate", str(sample), "--config", str(config)])
    # exit 1 because there are failures, but the preview reports the quarantine verdict.
    assert "Quarantine: 1 of 3 sampled rows would be quarantined" in result.output
    assert "within the quarantine threshold" in result.output


def test_validate_dry_run_reports_over_threshold(tmp_path):
    sample = tmp_path / "sample.csv"
    sample.write_text("id,amount\n1,a\n2,b\n3,3.5\n")  # 2 bad of 3 = 67% > 50%
    config = tmp_path / "pipeline.yaml"
    config.write_text(_pipeline(tmp_path, tmp_path / "dest.db", _Q(tmp_path)))
    result = CliRunner().invoke(cli, ["validate", str(sample), "--config", str(config)])
    assert "exceeds the quarantine threshold" in result.output
