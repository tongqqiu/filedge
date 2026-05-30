"""The orchestrator's ordering is the contract: a complete File + sidecar land in
the Watched Directory, the cursor advances only after promotion, an empty fetch
is a clean no-op, and a promotion failure leaves the cursor un-advanced. One
end-to-end test then ingests the promoted File with `filedge run`.
"""

import os

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.fetch.cursor_state import CursorStore
from filedge.fetch.orchestrator import run_fetch
from filedge.fetch.source_client import FetchResult
from filedge.source_manifest import discover_and_parse


class FakeClient:
    """Stand-in for HttpSourceClient — records the cursor it was asked to fetch from."""

    def __init__(self, result):
        self._result = result
        self.seen_cursor = "UNSET"

    def fetch(self, plan, cursor):
        self.seen_cursor = cursor
        return self._result


def _sources_yaml(tmp_path, *, gzip=False):
    staging = tmp_path / "staging"
    landing = tmp_path / "landing"
    state = tmp_path / "state"
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(
        "version: 1\n"
        "sources:\n"
        "  - name: commits\n"
        "    type: github\n"
        "    url: https://api.example/commits\n"
        f"    staging_dir: {staging}\n"
        f"    watched_directory: {landing}\n"
        f"    state_dir: {state}\n"
        f"    gzip: {str(gzip).lower()}\n"
        "    cursor:\n"
        "      param: since\n"
        "      field: updated_at\n"
    )
    return str(cfg), staging, landing, state


def _result(records, next_cursor):
    return FetchResult(records=records, next_cursor=next_cursor,
                       started_at="2026-05-30T00:00:00+00:00",
                       finished_at="2026-05-30T00:01:00+00:00")


def test_fetch_promotes_file_and_sidecar_and_advances_cursor(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    records = [{"id": 1, "updated_at": "2026-05-01"}, {"id": 2, "updated_at": "2026-05-02"}]

    outcome = run_fetch(cfg, "commits", client=FakeClient(_result(records, "2026-05-02")))

    assert outcome.record_count == 2
    assert outcome.to_cursor == "2026-05-02"
    landed = os.listdir(landing)
    assert sum(name.endswith(".ndjson") for name in landed) == 1
    assert sum(name.endswith(".manifest.json") for name in landed) == 1
    # Staging was drained — the Files were moved, not copied.
    assert os.listdir(staging) == []
    # Cursor advanced only after promotion.
    assert CursorStore(str(state)).read("commits") == "2026-05-02"
    # The promoted File's sidecar round-trips through the reader.
    result = discover_and_parse(outcome.data_path)
    assert result.metadata.source_name == "commits"
    assert result.metadata.record_count == 2


def test_empty_fetch_is_a_clean_noop(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)

    outcome = run_fetch(cfg, "commits", client=FakeClient(_result([], None)))

    assert outcome.skipped is True
    assert outcome.record_count == 0
    assert not landing.exists() or os.listdir(landing) == []
    assert CursorStore(str(state)).read("commits") is None


def test_fetch_resumes_from_the_stored_cursor(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    CursorStore(str(state)).advance("commits", "2026-05-01")
    client = FakeClient(_result([{"id": 3, "updated_at": "2026-05-03"}], "2026-05-03"))

    run_fetch(cfg, "commits", client=client)

    assert client.seen_cursor == "2026-05-01"


def test_promotion_failure_leaves_cursor_unadvanced(tmp_path, monkeypatch):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    CursorStore(str(state)).advance("commits", "2026-05-01")

    import filedge.fetch.orchestrator as orch

    def boom(*a, **k):
        raise OSError("landing zone unreachable")

    monkeypatch.setattr(orch, "promote", boom)

    with pytest.raises(OSError):
        run_fetch(cfg, "commits", client=FakeClient(_result(
            [{"id": 9, "updated_at": "2026-05-09"}], "2026-05-09")))

    # The window must be retried next run, not skipped.
    assert CursorStore(str(state)).read("commits") == "2026-05-01"


def test_dry_run_reports_window_without_promoting(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)

    outcome = run_fetch(cfg, "commits", dry_run=True)

    assert outcome.dry_run is True
    assert outcome.target_filename and outcome.target_filename.endswith(".ndjson")
    assert not landing.exists() or os.listdir(landing) == []


def test_promoted_file_is_ingestable_by_filedge_run(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    records = [{"id": 1, "updated_at": "2026-05-01"}, {"id": 2, "updated_at": "2026-05-02"}]
    outcome = run_fetch(cfg, "commits", client=FakeClient(_result(records, "2026-05-02")))

    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        "format: ndjson\n"
        "dest_table: commits\n"
        "connector:\n"
        f"  type: sqlite\n"
        f"  url: sqlite:///{tmp_path}/dest.db\n"
        "columns:\n"
        "  - source: id\n"
        "    dest: id\n"
        "    type: integer\n"
        "    required: true\n"
        "  - source: updated_at\n"
        "    dest: updated_at\n"
        "    type: string\n"
        "    required: true\n"
    )

    result = CliRunner().invoke(cli, [
        "run",
        "--dir", str(landing),
        "--config", str(config_file),
        "--audit-db-url", f"sqlite:///{tmp_path}/audit.db",
        "--no-progress",
    ])
    assert result.exit_code == 0, result.output
    assert "Committed: 1" in result.output

    # The Audit Record carries the Source Manifest provenance for the API File.
    lineage = CliRunner().invoke(cli, [
        "lineage", os.path.basename(outcome.data_path),
        "--audit-db-url", f"sqlite:///{tmp_path}/audit.db",
    ])
    assert lineage.exit_code == 0, lineage.output
    assert "commits" in lineage.output
