# etl-big-idea

A batch ETL system built around the reliability patterns that standard Airflow + Spark stacks often miss: atomic commits, content-based idempotency, automatic retry, and a full audit trail.

## The Problem

Most ETL pipelines fail silently in the worst possible way — they write half the rows before crashing, leaving the destination in a corrupt state with no record of what happened. Re-running the job then double-writes the rows that did succeed.

This project addresses three root causes:

1. **Partial load corruption** — a file's rows and its audit marker are committed in a single database transaction. Either both land or neither does.
2. **Filename-based idempotency** — files are identified by SHA-256 content hash, not filename. Renaming a file doesn't re-ingest it; replacing it with new content does.
3. **No audit trail** — every file passes through a `PENDING → PROCESSING → COMMITTED/FAILED` state machine, stored in an audit table alongside row-level provenance (`_source_file_hash`, `_ingested_at`).

## Features

- **Atomic commits** — rows + audit marker written together; rollback on any failure
- **Content-hash idempotency** — re-running the pipeline is always safe
- **Automatic retry** — failed files are retried up to `retry_cap` times across runs
- **Stale lock reclaim** — PROCESSING locks from crashed workers are reclaimed automatically
- **Strict validation** — whole file fails if any row fails type coercion or misses a required column
- **Column tolerance** — extra source columns are ignored; only declared columns are loaded
- **Row provenance** — every destination row carries `_source_file_hash` and `_ingested_at`
- **Schema guard** — auto-creates destination table on first run; refuses to silently alter it if columns are added later
- **Formats** — CSV and NDJSON (newline-delimited JSON)
- **Databases** — SQLite for development, PostgreSQL for production

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

**1. Write a pipeline config**

```yaml
# pipeline.yaml
format: csv
dest_table: orders
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
etl run --dir ./incoming --config pipeline.yaml --db-url sqlite:///etl.db
# Committed: 3  Failed: 0  Skipped: 0  New: 3  Reclaimed: 0  Retried: 0
```

**3. Check status**

```bash
etl status --db-url sqlite:///etl.db
# PENDING:    0
# PROCESSING: 0
# COMMITTED:  3
# FAILED:     0

etl status --db-url sqlite:///etl.db --json
```

The `--db-url` can also be set via the `ETL_DB_URL` environment variable.

## Column Types

| Type | Notes |
|------|-------|
| `string` | Any value coerced to str |
| `integer` | Whole numbers |
| `float` | Decimal numbers |
| `date` | ISO 8601 date (`YYYY-MM-DD`) |
| `timestamp` | ISO 8601 datetime |
| `boolean` | `true/1/yes` → True, `false/0/no` → False |

## How a Run Works

```
Run N
├── Reset FAILED files below retry_cap → PENDING
├── Reclaim stale PROCESSING locks → PENDING
├── Ensure destination table exists (create or validate schema)
├── Hash all files in watched directory
├── Enqueue new content hashes as PENDING
└── For each PENDING file:
    ├── Tx 1: mark PROCESSING (distributed lock)
    ├── Stream rows in batches, validate and coerce each row
    └── Tx 2: insert rows + mark COMMITTED  (or rollback + mark FAILED)
```

## Retry Behaviour

A file that fails validation is marked `FAILED` with `attempt_count = 1`. On the next run, if `attempt_count < retry_cap`, it is reset to `PENDING` and retried. Once `attempt_count >= retry_cap`, the file is terminal — it stays `FAILED` and is counted as `skipped` in the run summary.

To manually re-queue a terminal failure, update the audit record directly:

```sql
UPDATE etl_file_audit SET state='PENDING', attempt_count=0 WHERE content_hash='<hash>';
```

## Running Tests

```bash
pytest
```

61 tests covering hashing, config loading, DB state machine, parsing, type coercion, the full pipeline, and the CLI.

## Architecture Decisions

- [ADR-0001: Single-transaction commit](docs/adr/0001-single-transaction-commit.md)
- [ADR-0002: Content hash as idempotency key](docs/adr/0002-content-hash-as-idempotency-key.md)
- [ADR-0003: Strict-mode validation](docs/adr/0003-strict-mode-validation.md)
