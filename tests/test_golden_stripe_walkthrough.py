"""Golden regulated-ingestion walkthrough.

This is the executable spine for the Stripe-style API Source story:
Reference Fetcher -> complete File + Source Manifest -> Filedge Run -> DuckDB
Destination -> lineage -> Audit Export.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.fetch.orchestrator import run_fetch
from filedge.fetch.source_client import HttpSourceClient


def _sqlite(tmp_path, name):
    return f"sqlite:///{tmp_path}/{name}"


def test_stripe_style_api_source_to_audited_duckdb_load(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_walkthrough")
    staging = tmp_path / "staging"
    landing = tmp_path / "landing"
    state = tmp_path / "state"
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "version: 1\n"
        "sources:\n"
        "  - name: stripe-charges\n"
        "    type: stripe\n"
        "    resource: charges\n"
        "    credential_env: STRIPE_API_KEY\n"
        "    api_base: https://stripe.test\n"
        "    page_size: 2\n"
        f"    staging_dir: {staging}\n"
        f"    watched_directory: {landing}\n"
        f"    state_dir: {state}\n"
    )

    pages = {
        None: {
            "object": "list",
            "has_more": True,
            "data": [
                {
                    "id": "ch_001",
                    "created": 1770000001,
                    "amount": 1250,
                    "currency": "usd",
                    "status": "succeeded",
                },
                {
                    "id": "ch_002",
                    "created": 1770000002,
                    "amount": 2500,
                    "currency": "usd",
                    "status": "succeeded",
                },
            ],
        },
        "ch_002": {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "ch_003",
                    "created": 1770000003,
                    "amount": 999,
                    "currency": "usd",
                    "status": "refunded",
                },
            ],
        },
    }
    seen_headers = []

    def stripe_transport(url, headers):
        seen_headers.append(dict(headers))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "stripe.test"
        assert parsed.path == "/v1/charges"
        assert query["limit"] == ["2"]
        page = pages[query.get("starting_after", [None])[0]]
        return 200, {}, json.dumps(page).encode()

    outcome = run_fetch(
        str(sources),
        "stripe-charges",
        client=HttpSourceClient(
            stripe_transport,
            sleep=lambda seconds: None,
            now=lambda: "2026-06-01T12:00:00+00:00",
        ),
    )

    assert outcome.record_count == 3
    assert outcome.to_cursor == "1770000003"
    assert seen_headers
    assert all(h["Authorization"] == "Bearer sk_test_walkthrough" for h in seen_headers)
    assert sum(p.suffix == ".ndjson" for p in landing.iterdir()) == 1
    assert sum(p.name.endswith(".ndjson.manifest.json") for p in landing.iterdir()) == 1

    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        "format: ndjson\n"
        "dest_table: stripe_charges\n"
        "source_manifest: required\n"
        "connector:\n"
        "  type: duckdb\n"
        f"  path: {tmp_path / 'stripe.duckdb'}\n"
        "columns:\n"
        "  - source: id\n"
        "    dest: charge_id\n"
        "    type: string\n"
        "    required: true\n"
        "  - source: created\n"
        "    dest: created_at_epoch\n"
        "    type: integer\n"
        "    required: true\n"
        "  - source: amount\n"
        "    dest: amount_cents\n"
        "    type: integer\n"
        "    required: true\n"
        "  - source: currency\n"
        "    dest: currency\n"
        "    type: string\n"
        "    required: true\n"
        "  - source: status\n"
        "    dest: status\n"
        "    type: string\n"
        "    required: true\n"
    )

    runner = CliRunner()
    audit = _sqlite(tmp_path, "audit.db")
    run = runner.invoke(
        cli,
        [
            "run",
            "--dir",
            str(landing),
            "--config",
            str(pipeline),
            "--audit-db-url",
            audit,
            "--json",
        ],
    )
    assert run.exit_code == 0, run.output
    summary = json.loads(run.output.strip().splitlines()[-1])
    assert summary["committed"] == 1
    assert summary["rows_committed"] == 3

    import duckdb

    rows = duckdb.connect(str(tmp_path / "stripe.duckdb")).execute(
        "select charge_id, amount_cents, status from stripe_charges order by charge_id"
    ).fetchall()
    assert rows == [
        ("ch_001", 1250, "succeeded"),
        ("ch_002", 2500, "succeeded"),
        ("ch_003", 999, "refunded"),
    ]

    lineage = runner.invoke(
        cli,
        [
            "lineage",
            outcome.data_path.split("/")[-1],
            "--audit-db-url",
            audit,
            "--dest-table",
            "stripe_charges",
        ],
    )
    assert lineage.exit_code == 0, lineage.output
    assert "source_type:      stripe" in lineage.output
    assert "source_name:      stripe-charges" in lineage.output
    assert "record_count:     3" in lineage.output
    assert "resource: charges" in lineage.output

    audit_export = tmp_path / "audit-export" / "index.html"
    exported = runner.invoke(
        cli,
        [
            "export-audit",
            "--audit-db-url",
            audit,
            "--output",
            str(audit_export),
            "--title",
            "Stripe Charges",
            "--dest-table",
            "stripe_charges",
        ],
    )
    assert exported.exit_code == 0, exported.output
    assert "Exported 1 file records" in exported.output
    assert audit_export.exists()
    assert "stripe-charges" in audit_export.read_text()
