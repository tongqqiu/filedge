# Filedge

A batch ETL system built around the reliability patterns that standard Airflow + Spark stacks often miss: atomic commits, content-based idempotency, automatic retry, and a full audit trail — with pluggable destination support for PostgreSQL, BigQuery, Databricks, and SQLite.

## The Problem

Most ETL pipelines fail silently in the worst possible way — they write half the rows before crashing, leaving the destination in a corrupt state with no record of what happened. Re-running the job then double-writes the rows that did succeed.

This project addresses three root causes:

1. **Partial load corruption** — rows and the audit marker are written atomically. Either both land or neither does.
2. **Filename-based idempotency** — files are identified by SHA-256 content hash, not filename. Renaming a file doesn't re-ingest it; replacing it with new content does.
3. **No audit trail** — every file passes through a `PENDING → PROCESSING → COMMITTED/FAILED` state machine, stored in a dedicated audit database alongside row-level provenance (`_source_file_hash`, `_ingested_at`).

## Features

- **Pluggable destinations** — SQLite, PostgreSQL, BigQuery, Databricks via a `connector:` block in `pipeline.yaml`
- **Connector-level idempotency** — retrying a failed file never produces duplicate rows
- **Write modes** — `append` (default) or `truncate` per pipeline
- **Automatic retry** — failed files are retried up to `retry_cap` times across runs
- **Stale lock reclaim** — PROCESSING locks from crashed workers are reclaimed automatically
- **Strict validation** — whole file fails if any row fails type coercion or misses a required column
- **Column tolerance** — extra source columns are ignored; only declared columns are loaded
- **Row provenance** — every destination row carries `_source_file_hash` and `_ingested_at`
- **Schema guard** — auto-creates destination table on first run; refuses to silently alter it if columns are added later
- **Formats** — CSV and NDJSON (newline-delimited JSON)

## Installation

Requires [uv](https://docs.astral.sh/uv/).

```bash
# Core only (SQLite destination, no extra SDKs needed)
uv sync --extra dev

# With PostgreSQL destination support
uv sync --extra dev --extra postgres

# With BigQuery destination support
uv sync --extra dev --extra bigquery

# With Databricks destination support
uv sync --extra dev --extra databricks
```

## Quick Start

**1. Write a pipeline config**

```yaml
# pipeline.yaml
format: csv
dest_table: orders
write_mode: append   # or: truncate
retry_cap: 3
stale_timeout_minutes: 30
batch_size: 1000

columns:
  - source: order_id
    dest: order_id
    type: string
    required: true
  - source: amount
    dest: amount
    type: float
    required: true
  - source: order_date
    dest: order_date
    type: date
    required: false
```

**2. Run the pipeline**

```bash
filedge run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///filedge.db
# Committed: 3  Failed: 0  Skipped: 0  New: 3  Reclaimed: 0  Retried: 0
```

**3. Check status**

```bash
filedge status --audit-db-url sqlite:///filedge.db
# PENDING:    0
# PROCESSING: 0
# COMMITTED:  3
# FAILED:     0

filedge status --audit-db-url sqlite:///filedge.db --json
```

`--audit-db-url` can also be set via `FILEDGE_AUDIT_DB_URL`.

## Connectors

The destination is configured via an optional `connector:` block in `pipeline.yaml`. If omitted, the connector is inferred from `--audit-db-url` (backward compatible).

### SQLite (default for local dev)

```yaml
# No connector block needed — inferred from --audit-db-url sqlite:///...
```

### PostgreSQL

```yaml
connector:
  type: postgres
  url: postgresql://user:pass@host/dbname
```

Or set `DATABASE_URL` in the environment and omit `url`.

### BigQuery

```yaml
connector:
  type: bigquery
  project: my-gcp-project
  dataset: my_dataset
```

Credentials from `GOOGLE_APPLICATION_CREDENTIALS` or Application Default Credentials. Requires `pip install filedge[bigquery]`.

Live BigQuery integration tests are opt-in:

```bash
export FILEDGE_BIGQUERY_INTEGRATION=1
export BIGQUERY_PROJECT=my-gcp-project
export BIGQUERY_DATASET=filedge_ci_test
uv sync --extra dev --extra bigquery
uv run pytest tests/test_connector_bigquery.py
```

### Databricks

```yaml
connector:
  type: databricks
  server_hostname: adb-xxx.azuredatabricks.net
  http_path: /sql/1.0/warehouses/xxx
  catalog: main
  schema: default
```

Auth token from `DATABRICKS_TOKEN`. Requires `pip install filedge[databricks]`.

Note: the Databricks connector has unit-style coverage, but no live Databricks integration test suite yet. A live test needs a SQL warehouse and a staging location that the warehouse can read with `COPY INTO`.

## Write Modes

| Mode | Behaviour | Idempotency |
|------|-----------|-------------|
| `append` (default) | Rows added alongside prior records | Delete-where-hash then insert on retry |
| `truncate` | Table wiped then replaced with this file's rows | Inherently idempotent |

## Column Types

| Type | PostgreSQL | BigQuery | SQLite |
|------|-----------|---------|--------|
| `string` | TEXT | STRING | TEXT |
| `integer` | INTEGER | INT64 | INTEGER |
| `float` | DOUBLE PRECISION | FLOAT64 | REAL |
| `date` | DATE | DATE | TEXT |
| `timestamp` | TIMESTAMP WITH TIME ZONE | TIMESTAMP | TEXT |
| `boolean` | BOOLEAN | BOOL | INTEGER |

## How a Run Works

```
Run N
├── Reset FAILED files below retry_cap → PENDING
├── Reclaim stale PROCESSING locks → PENDING
├── Connector: ensure destination table exists (create or validate schema)
├── Hash all files in watched directory
├── Enqueue new content hashes as PENDING
└── For each PENDING file:
    ├── Audit DB: mark PROCESSING (distributed lock)
    ├── Connector: write_rows — stream rows through parser + transform
    │   └── Connector commits its own transaction (idempotent per file_hash)
    └── Audit DB: mark COMMITTED  (or mark FAILED on error)
```

The audit DB and destination are separate systems. A crash between the connector write and the audit mark leaves the file in PROCESSING — the stale-lock reclaim picks it up on the next run and the connector's idempotency ensures no duplicate rows.

## Retry Behaviour

A file that fails validation is marked `FAILED` with `attempt_count = 1`. On the next run, if `attempt_count < retry_cap`, it is reset to `PENDING` and retried. Once `attempt_count >= retry_cap`, the file is terminal — it stays `FAILED` and is counted as `skipped` in the run summary.

To manually re-queue a terminal failure:

```sql
UPDATE etl_file_audit SET state='PENDING', attempt_count=0 WHERE content_hash='<hash>';
```

## Running Tests

```bash
uv run pytest
```

72 tests covering hashing, config loading, audit DB state machine, parsing, type coercion, connector contracts (SQLite), registry, the full pipeline, and the CLI.

## Architecture Decisions

- [ADR-0001: Single-transaction commit](docs/adr/0001-single-transaction-commit.md)
- [ADR-0002: Content hash as idempotency key](docs/adr/0002-content-hash-as-idempotency-key.md)
- [ADR-0003: Strict-mode validation](docs/adr/0003-strict-mode-validation.md)
- [ADR-0004: Audit DB / Connector split](docs/adr/0004-audit-connector-split.md)
