"""The orchestrator's ordering is the contract: a complete File + sidecar land in
the Watched Directory, the cursor advances only after promotion, an empty fetch
is a clean no-op, and a promotion failure leaves the cursor un-advanced. One
end-to-end test then ingests the promoted File with `filedge run`.
"""

import json
import os

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.fetch.cursor_state import CursorStore
from filedge.fetch.orchestrator import run_fetch
from filedge.fetch.source_client import HttpSourceClient
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


def test_edgar_fetch_lands_manifest_and_ingestable_fact_file(tmp_path):
    staging = tmp_path / "staging"
    landing = tmp_path / "landing"
    state = tmp_path / "state"
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(
        "version: 1\n"
        "sources:\n"
        "  - name: apple-revenues\n"
        "    type: edgar\n"
        "    cik: 320193\n"
        "    concept: Revenues\n"
        "    unit: USD\n"
        "    user_agent: Filedge Test contact@example.com\n"
        f"    staging_dir: {staging}\n"
        f"    watched_directory: {landing}\n"
        f"    state_dir: {state}\n"
        "    cursor:\n"
        "      field: filed\n"
    )
    seen = {}
    facts = [
        {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-01", "val": 100},
        {"fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-05-01", "val": 125},
    ]

    def transport(url, headers):
        seen["url"] = url
        seen["headers"] = dict(headers)
        return 200, {}, json.dumps({"units": {"USD": facts}}).encode()

    outcome = run_fetch(
        str(cfg),
        "apple-revenues",
        client=HttpSourceClient(
            transport,
            sleep=lambda s: None,
            now=lambda: "2026-05-30T00:00:00+00:00",
        ),
    )

    assert seen["url"] == (
        "https://data.sec.gov/api/xbrl/companyconcept/"
        "CIK0000320193/us-gaap/Revenues.json"
    )
    assert seen["headers"]["User-Agent"] == "Filedge Test contact@example.com"
    assert outcome.record_count == 2
    assert CursorStore(str(state)).read("apple-revenues") == "2026-05-01"
    manifest = discover_and_parse(outcome.data_path)
    assert manifest.error_category is None
    assert manifest.metadata.source_type == "edgar"
    assert manifest.metadata.source_name == "apple-revenues"
    assert manifest.metadata.source_range == {
        "cursor_param": "filed",
        "cursor_field": "filed",
        "from": None,
        "to": "2026-05-01",
        "cik": "0000320193",
        "taxonomy": "us-gaap",
        "concept": "Revenues",
        "unit": "USD",
    }

    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        "format: ndjson\n"
        "dest_table: edgar_facts\n"
        "connector:\n"
        "  type: sqlite\n"
        f"  url: sqlite:///{tmp_path}/dest.db\n"
        "columns:\n"
        "  - source: filed\n"
        "    dest: filed\n"
        "    type: string\n"
        "    required: true\n"
        "  - source: val\n"
        "    dest: value\n"
        "    type: integer\n"
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


def test_edgar_second_fetch_with_no_newer_facts_is_noop(tmp_path):
    staging = tmp_path / "staging"
    landing = tmp_path / "landing"
    state = tmp_path / "state"
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(
        "version: 1\n"
        "sources:\n"
        "  - name: apple-revenues\n"
        "    type: edgar\n"
        "    cik: 320193\n"
        "    concept: Revenues\n"
        "    unit: USD\n"
        "    user_agent: Filedge Test contact@example.com\n"
        f"    staging_dir: {staging}\n"
        f"    watched_directory: {landing}\n"
        f"    state_dir: {state}\n"
        "    cursor:\n"
        "      field: filed\n"
    )
    facts = [{"filed": "2026-02-01", "val": 100}]

    def transport(url, headers):
        return 200, {}, json.dumps({"units": {"USD": facts}}).encode()

    client = HttpSourceClient(
        transport,
        sleep=lambda s: None,
        now=lambda: "2026-05-30T00:00:00+00:00",
    )
    first = run_fetch(str(cfg), "apple-revenues", client=client)
    second = run_fetch(str(cfg), "apple-revenues", client=client)

    assert first.record_count == 1
    assert second.skipped is True
    assert second.record_count == 0
    assert os.listdir(landing)
    assert sum(name.endswith(".ndjson") for name in os.listdir(landing)) == 1


def _stripe_sources_yaml(tmp_path):
    staging = tmp_path / "staging"
    landing = tmp_path / "landing"
    state = tmp_path / "state"
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(
        "version: 1\n"
        "sources:\n"
        "  - name: charges\n"
        "    type: stripe\n"
        "    resource: charges\n"
        "    credential_env: STRIPE_API_KEY\n"
        f"    staging_dir: {staging}\n"
        f"    watched_directory: {landing}\n"
        f"    state_dir: {state}\n"
    )
    return str(cfg), staging, landing, state


def test_stripe_fetch_records_resource_in_manifest_source_range(tmp_path):
    cfg, staging, landing, state = _stripe_sources_yaml(tmp_path)
    records = [
        {"id": "ch_1", "created": 1700000001},
        {"id": "ch_2", "created": 1700000002},
    ]

    outcome = run_fetch(cfg, "charges", client=FakeClient(_result(records, "1700000002")))

    assert outcome.record_count == 2
    assert outcome.to_cursor == "1700000002"
    result = discover_and_parse(outcome.data_path)
    assert result.metadata.source_type == "stripe"
    assert result.metadata.source_range["resource"] == "charges"
    assert result.metadata.source_range["cursor_field"] == "created"
    assert result.metadata.source_range["to"] == "1700000002"
