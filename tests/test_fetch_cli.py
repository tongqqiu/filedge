"""The `filedge-fetch` command renders each outcome branch and exits non-zero on
a FetchError. run_fetch itself is exercised in test_fetch_orchestrator; here we
cover the CLI's output and exit-code wiring.
"""

from click.testing import CliRunner

from filedge.fetch.cli import fetch
from filedge.fetch.errors import SourcesConfigError
from filedge.fetch.orchestrator import FetchOutcome


def _config(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("version: 1\nsources: []\n")  # content irrelevant; run_fetch is patched
    return str(path)


def test_success_reports_counts_and_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "filedge.fetch.cli.run_fetch",
        lambda *a, **k: FetchOutcome(
            source_name="commits", record_count=2,
            from_cursor="2026-05-01", to_cursor="2026-05-02",
            data_path="/landing/commits-x.ndjson",
        ),
    )
    result = CliRunner().invoke(fetch, ["--config", _config(tmp_path), "--source", "commits"])
    assert result.exit_code == 0
    assert "fetched 2 records" in result.output
    assert "/landing/commits-x.ndjson" in result.output


def test_skipped_reports_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "filedge.fetch.cli.run_fetch",
        lambda *a, **k: FetchOutcome(
            source_name="commits", record_count=0,
            from_cursor="2026-05-02", to_cursor="2026-05-02", skipped=True,
        ),
    )
    result = CliRunner().invoke(fetch, ["--config", _config(tmp_path), "--source", "commits"])
    assert result.exit_code == 0
    assert "no new records" in result.output


def test_dry_run_reports_window(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "filedge.fetch.cli.run_fetch",
        lambda *a, **k: FetchOutcome(
            source_name="commits", record_count=0, from_cursor=None, to_cursor=None,
            dry_run=True, target_filename="commits-none-none-DRYRUN.ndjson",
        ),
    )
    result = CliRunner().invoke(
        fetch, ["--config", _config(tmp_path), "--source", "commits", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "[dry-run]" in result.output
    assert "commits-none-none-DRYRUN.ndjson" in result.output


def test_fetch_error_exits_nonzero(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise SourcesConfigError("No API Source 'nope'. Known: 'commits'.")

    monkeypatch.setattr("filedge.fetch.cli.run_fetch", boom)
    result = CliRunner().invoke(fetch, ["--config", _config(tmp_path), "--source", "nope"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "nope" in result.output
