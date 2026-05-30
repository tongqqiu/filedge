# Filedge

[![CI](https://github.com/tongqqiu/filedge/actions/workflows/ci.yml/badge.svg)](https://github.com/tongqqiu/filedge/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/tongqqiu/filedge/branch/main/graph/badge.svg)](https://codecov.io/gh/tongqqiu/filedge)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Files are the universal building block of data engineering.** Whether data starts in Kafka, Stripe's API, a partner SFTP, or a CDC stream, every reliable pipeline eventually crystallizes it into a file before it touches the warehouse. Filedge is the load boundary built around that fact: atomic per-file ingestion, content-hash idempotency, and a full audit trail — into SQLite, PostgreSQL, BigQuery, Databricks, or DuckDB.

## Why files?

Streams are continuous; files are discrete. That discreteness is what makes ingestion *auditable*: a file has a SHA-256, a row count, a state in the audit DB, and a row-level provenance trail in the destination. Every downstream question — *did we load this?*, *replay this*, *where did this row come from?* — has a deterministic anchor.

Filedge starts where the file lands and ends when its rows are committed. Upstream is your choice: [dlt](https://dlthub.com) or vendor exporters for APIs, [Kafka Connect](https://docs.confluent.io/platform/current/connect/index.html) or Vector for queues, rclone for SFTP. Downstream is your warehouse. The hard part in between — retry-safe commits, dedupe, retries, lineage — is all Filedge does.

## What it gives you that a hand-rolled DAG doesn't

| Failure mode | Typical pipeline | Filedge |
|---|---|---|
| Half-written tables after a crash | Manual cleanup | Per-file atomic commit, retry-safe by content hash |
| "Did we already load this file?" | Filename heuristics | SHA-256 dedupe at the entry point |
| "Where did this row come from?" | Grep logs | `_source_file_hash` + `_ingested_at` on every row |
| Stale lock from a killed worker | Page someone | Reclaimed automatically on next run |
| One bad file blocks the pipeline | Skip and forget | Bounded retry → terminal FAILED with audit |
| Schema drift in destination | Silent corruption | Loud failure with a clear diff |

## How it differs from neighbors

- **vs Airbyte / Fivetran / dlt** — those *fetch* (paginate APIs, manage cursors). Filedge *lands* — it takes whatever they produce as files and makes the write to the warehouse audit-grade. Use them as Fetchers in front of Filedge.
- **vs Kafka Connect / Flink / Spark Structured Streaming** — streaming systems own continuous offsets and incremental state. Filedge owns the *file* as the unit of work — simpler to reason about, replay, and audit. Materialize queues to files, then ingest.
- **vs Airflow + custom Python loaders** — same DAG shape, but partial-load corruption, lock reclaim, retry caps, idempotent CDC apply, and row provenance are already wired in.
- **vs Iceberg / Delta tables** — those are *table formats*. Filedge is what *writes to them* (or to plain BigQuery / Postgres / Databricks tables) with the per-file commit guarantee.

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev                          # core (SQLite)
uv sync --extra dev --extra postgres         # + PostgreSQL
uv sync --extra dev --extra bigquery         # + BigQuery
uv sync --extra dev --extra databricks       # + Databricks
uv sync --extra dev --extra duckdb           # + DuckDB
uv sync --extra dev --extra authoring        # + Authoring UI
uv sync --extra dev --extra excel            # + Excel (.xlsx)
uv sync --extra dev --extra kafka            # + Reference Queue Materializer
```

Declare a pipeline:

```yaml
# pipeline.yaml
format: csv
dest_table: orders
write_mode: append          # append | truncate | cdc
retry_cap: 3
batch_size: 1000

connector:
  type: sqlite
  url: sqlite:///orders.db

columns:
  - { source: order_id,   dest: order_id,   type: string,  required: true }
  - { source: amount,     dest: amount,     type: float,   required: true }
  - { source: order_date, dest: order_date, type: date }
```

Run it:

```bash
filedge run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///filedge.db
# Committed: 3  Failed: 0  Skipped: 0  New: 3  Reclaimed: 0  Retried: 0

filedge status --audit-db-url sqlite:///filedge.db
# PENDING: 0  PROCESSING: 0  COMMITTED: 3  FAILED: 0
```

Don't know the schema yet? `filedge inspect data.csv` samples the file and prints a `columns:` block with confidence tiers ready to paste.

Prefer to author interactively? `filedge author data.csv` launches a local terminal UI that runs schema inference, lets you review columns, write modes, connectors, and field encryption, validates the result, and writes a ready-to-run pipeline folder. To revise a pipeline later, `filedge author --pipeline pipelines/<id>` re-opens it in place — or run `filedge author` with no arguments to browse and pick from the registry. See the [author guide](docs/guides/author.md).

Pulling from APIs or queues? Use an upstream Fetcher or Queue Materializer to
land complete Files, then run Filedge. The first-party companions
`filedge-fetch` and `filedge-materialize` demonstrate the audited
materialize-to-files contract for API Sources, EDGAR `companyConcept`, and
Kafka Queue Sources. See the [API sources](docs/guides/api-sources.md) and
[queue sources](docs/guides/queue-sources.md) guides.

## Connectors

The destination is configured via a `connector:` block in `pipeline.yaml`. Built-ins:

| Destination  | Extra        | Notes |
|---|---|---|
| SQLite       | (core)       | Default for local dev; configure with `type: sqlite` and a `url` |
| PostgreSQL   | `postgres`   | `COPY` bulk load; idempotent via per-hash DELETE |
| BigQuery     | `bigquery`   | NDJSON staging + load job; job-ID-keyed idempotency (7-day window) |
| Databricks   | `databricks` | Unity Catalog volume staging |
| DuckDB       | `duckdb`     | File-based; single-writer, fails fast if locked |

See [docs/guides/run.md](docs/guides/run.md) for full connector config, credentials, and live-integration test setup.

## How a run works

```
filedge run
├── Reset FAILED below retry_cap → PENDING
├── Reclaim stale PROCESSING locks → PENDING
├── Connector: ensure destination table exists
├── Hash files in watched dir; enqueue new hashes as PENDING
└── For each PENDING file:
    ├── Audit DB: mark PROCESSING        (distributed lock)
    ├── Connector: stream rows → commit  (idempotent per file_hash)
    └── Audit DB: mark COMMITTED / FAILED
```

The audit DB and the destination are separate systems. A crash between connector commit and audit mark leaves the file PROCESSING — the next run reclaims it, and the connector's per-hash idempotency guarantees no duplicate rows.

## More

- Guides: [author](docs/guides/author.md) · [run](docs/guides/run.md) · [scale](docs/guides/scale.md) · [inspect](docs/guides/inspect.md) · [preview](docs/guides/preview.md) · [validate](docs/guides/validate.md) · [compact](docs/guides/compact.md) · [healthcheck](docs/guides/healthcheck.md) · [requeue](docs/guides/requeue.md) · [audit export](docs/guides/audit-export.md) · [CDC files](docs/guides/cdc-files.md) · [API sources](docs/guides/api-sources.md) · [queue sources](docs/guides/queue-sources.md) · [source manifests](docs/guides/source-manifests.md)
- Release notes: [CHANGELOG.md](CHANGELOG.md)
- Domain model: [CONTEXT.md](CONTEXT.md)
- Architecture decisions:
  - [ADR-0001: Single-transaction commit](docs/adr/0001-single-transaction-commit.md)
  - [ADR-0002: Content hash as idempotency key](docs/adr/0002-content-hash-as-idempotency-key.md)
  - [ADR-0003: Strict-mode validation](docs/adr/0003-strict-mode-validation.md)
  - [ADR-0004: Audit DB / Connector split](docs/adr/0004-audit-connector-split.md)
  - [ADR-0005: SFTP out of scope](docs/adr/0005-sftp-out-of-scope.md)
  - [ADR-0006: API sources fetched to files](docs/adr/0006-api-sources-fetched-to-files.md)
  - [ADR-0007: Queue source ingestion model](docs/adr/0007-queue-source-ingestion-model.md)
  - [ADR-0008: Schema inference confidence tiers](docs/adr/0008-schema-inference-confidence-tiers.md)
  - [ADR-0009: Warehouse CDC applied-file markers](docs/adr/0009-warehouse-cdc-applied-file-markers.md)
  - [ADR-0010: Audit export static site](docs/adr/0010-audit-export-static-site.md)
  - [ADR-0011: Source manifest and lineage](docs/adr/0011-source-manifest-and-lineage.md)
  - [ADR-0012: Excel format support](docs/adr/0012-excel-format-support.md)
  - [ADR-0013: Fixed-width format support](docs/adr/0013-fixed-width-format-support.md)
  - [ADR-0014: Column-level field encryption](docs/adr/0014-column-level-field-encryption.md)
  - [ADR-0015: Control and Audit Platform starts with local Pipeline Authoring](docs/adr/0015-control-and-audit-platform-starts-with-local-pipeline-authoring.md)
  - [ADR-0016: Authoring UI — Textual TUI](docs/adr/0016-authoring-ui-textual-tui.md)
  - [ADR-0017: Pipeline Folder and Pipeline Registry layout](docs/adr/0017-pipeline-folder-and-registry-layout.md)
  - [ADR-0018: Reference Fetcher external companion](docs/adr/0018-reference-fetcher-external-companion.md)
  - [ADR-0019: Dead-Letter Quarantine](docs/adr/0019-dead-letter-quarantine.md)

## License

Apache 2.0 — see [LICENSE](LICENSE).
