# ETL Reliability Pipeline — Product Requirements Document

!!! note "Historical planning document"
    This PRD records the original MVP shape. The current implementation uses the `filedge` package and CLI, requires a `connector:` block in `pipeline.yaml`, and follows ADR-0004's Audit DB / Destination connector split.

## Problem Statement

Engineers who ingest raw files into data warehouses using standard tooling (Airflow + Spark + cloud data warehouses) are missing fundamental reliability primitives. When a pipeline job fails mid-run, it leaves the destination in a half-written state — causing the next retry to produce duplicate records, skip records, or both. There is no reliable audit trail linking destination rows back to their source files, making data quality investigations slow and often impossible. Re-running a failed job is unsafe without manual intervention, which defeats the purpose of automation.

## Solution

A lightweight Python batch ETL system that treats **Partial Load Corruption** as the primary enemy and eliminates it by design. Every file ingestion is atomic: the destination records and the audit marker are written in a single database transaction, so either both land or neither does. Files are identified by **Content Hash** (SHA-256), making retries unconditionally safe — the system will never load the same data twice, regardless of filename. Every destination row carries provenance columns (`_source_file_hash`, `_ingested_at`) that make the audit trail self-contained and queryable. The system is operated via a short-lived CLI process (`filedge run`) triggered by any external scheduler, with `filedge status` for observability.

## User Stories

1. As a data engineer, I want ingested files to be identified by content hash rather than filename, so that re-dropping a file with the same name never causes duplicate loads.
2. As a data engineer, I want a failed mid-run job to leave no partial data in the destination, so that I can safely retry without manual cleanup.
3. As a data engineer, I want the audit marker and destination records written in a single transaction, so that there is no window where records exist but the marker does not (or vice versa).
4. As a data engineer, I want every destination row to carry `_source_file_hash` and `_ingested_at` columns, so that I can trace any row back to the exact file it came from.
5. As a data engineer, I want file ingestion state tracked as `PENDING → PROCESSING → COMMITTED / FAILED`, so that I can see precisely where a file is in the pipeline at any time.
6. As a data engineer, I want a file that has already been `COMMITTED` to be silently skipped on subsequent runs, so that replaying the pipeline is always safe.
7. As a data engineer, I want stale `PROCESSING` locks reclaimed automatically at the start of each run, so that a crashed worker does not permanently block a file.
8. As a data engineer, I want failed files to be retried automatically up to a configurable cap (default: 3), so that transient failures heal without human intervention.
9. As a data engineer, I want a file that exceeds the retry cap to enter terminal `FAILED` state, so that a persistently broken file does not burn retries forever.
10. As an operator, I want to manually re-queue a terminal `FAILED` file by resetting its state to `PENDING`, so that I can retry it after fixing the source data.
11. As a data engineer, I want to declare the pipeline schema in a `pipeline.yaml` file co-located with the source directory, so that schema changes do not require code changes.
12. As a data engineer, I want the `pipeline.yaml` to declare source column names, destination column names, types, and required-vs-optional status, so that the transform step is fully configuration-driven.
13. As a data engineer, I want the destination table to be created automatically on first run from the `pipeline.yaml` schema, so that I do not have to write DDL by hand.
14. As a data engineer, I want the system to fail loudly if the live destination table schema mismatches the `pipeline.yaml`, so that schema drift is caught before any data is loaded.
15. As a data engineer, I want the system to silently ignore extra columns in source files that are not declared in `pipeline.yaml`, so that upstream additions do not break my pipeline.
16. As a data engineer, I want the system to fail the entire file if a required column is missing, so that data contract violations are caught immediately.
17. As a data engineer, I want the entire file to fail if any row fails schema validation (strict mode), so that I can reason about whether the destination table is complete.
18. As a data engineer, I want files to be streamed and inserted in configurable batches (default: 1,000 rows), so that large files do not exhaust memory.
19. As a data engineer, I want to ingest CSV and newline-delimited JSON files, so that the most common raw data formats are supported out of the box.
20. As a data engineer, I want the file format to be declared in `pipeline.yaml`, so that the system detects it without relying on file extension conventions.
21. As a data engineer, I want string values in source files to be coerced to declared types (integer, float, date, timestamp, boolean), so that the destination table contains correctly typed data.
22. As a data engineer, I want an `etl run --dir <path> --config <path> --db-url <url>` command, so that I can trigger a pipeline run from any external scheduler (cron, Airflow, Kubernetes CronJob).
23. As an operator, I want `etl status --db-url <url>` to print file counts by state and recent failures, so that I can see system health at a glance.
24. As an operator, I want `etl status --json` to output machine-readable JSON, so that I can integrate the status check into monitoring and alerting scripts.
25. As a developer, I want to run the full system locally against SQLite without standing up PostgreSQL, so that local development and testing requires no infrastructure.
26. As a developer, I want the pipeline to target PostgreSQL in production with the same SQL and behavior as SQLite, so that local tests are representative of production.
27. As a data engineer, I want re-dropped files with the same filename but different content to both be loaded as separate audit records, so that corrections are not silently dropped.
28. As a data engineer, I want two files with identical content but different filenames to produce a single load, so that accidental duplicate drops are deduplicated.
29. As a developer, I want each pipeline component (parser, transform, loader, audit DB) to have a narrow, stable interface, so that I can test each in isolation and swap implementations independently.

## Implementation Decisions

### Module breakdown

The system is divided into six deep modules with well-defined interfaces, plus two thin coordination layers:

- **`etl.hashing`** — Computes SHA-256 content hash of a file via streaming reads. Interface: `compute_hash(file_path) → str`. No dependencies on other modules.

- **`etl.config`** — Loads and validates `pipeline.yaml`. Produces `PipelineConfig` (format, dest_table, columns, retry_cap, stale_timeout_minutes, batch_size) and `ColumnMapping` (source, dest, type, required) dataclasses. Interface: `load_config(path) → PipelineConfig`.

- **`etl.db`** — Database access layer. Wraps a SQLite or PostgreSQL connection behind a unified interface that normalises parameter placeholders. Owns the `etl_file_audit` table DDL and all audit CRUD operations: `insert_pending`, `find_file_by_hash`, `claim_processing`, `mark_committed`, `mark_failed`, `reclaim_stale_processing`, `get_status_summary`. Also owns `ensure_destination_table` (auto-create + mismatch detection) and `Database` connection wrapper.

- **`etl.parser`** — Abstract `Parser` base class with `parse(file_path) → Iterator[dict]` interface. Concrete implementations: `CSVParser`, `NDJSONParser`. Factory: `get_parser(format) → Parser`. Adding new formats requires only a new `Parser` subclass.

- **`etl.transform`** — Pure function `transform_row(row, columns) → dict`. Applies column name mapping and type coercion. Raises `TransformError` on missing required columns or type coercion failure. Supported types: `string`, `integer`, `float`, `date` (ISO 8601), `timestamp` (ISO 8601), `boolean`. Extra source columns are silently dropped (Column Tolerance).

- **`etl.loader`** — Streams a file through parser + transform and inserts rows in configurable batches within an open database transaction. Interface: `load_file(db, config, file_path, file_hash) → (rows_loaded, error_or_None)`. Does not commit — the caller commits both the inserted rows and the audit marker update in a single `db.commit()` call, implementing the single-transaction Commit guarantee (ADR-0001).

- **`etl.pipeline`** — Orchestration layer. `run_pipeline(watched_dir, config_path, db_url) → dict`. Calls all other modules in sequence: reclaim stale locks → ensure table → scan directory → enqueue PENDING → claim PROCESSING → load → commit or rollback+fail.

- **`etl.cli`** — Click-based CLI. Thin wrapper over `run_pipeline` and `get_status_summary`. Commands: `filedge run`, `filedge status`.

### Single-transaction commit

`etl.loader.load_file` inserts rows within the caller's open transaction but does not commit. After a successful `load_file`, the caller calls `db.mark_committed(hash)` and then `db.commit()` — a single commit that atomically lands both the records and the `COMMITTED` marker. On failure, `db.rollback()` undoes any partial inserts, after which `db.mark_failed` and `db.commit()` record the failure. This is the core correctness guarantee (ADR-0001).

### Content hash as idempotency key (ADR-0002)

`etl_file_audit.content_hash` has a UNIQUE constraint. `insert_pending` is a no-op if the hash already exists. At the start of each Run, `find_file_by_hash` gates every discovered file — anything already `COMMITTED` is silently skipped.

### Strict mode (ADR-0003)

`etl.transform.transform_row` raises `TransformError` on the first invalid row. `etl.loader.load_file` catches this, returns `(rows_loaded_so_far, error_string)`, and the caller performs `db.rollback()`. No partial commit occurs.

### Stale lock reclaim

At the start of each Run, `reclaim_stale_processing` updates any `PROCESSING` record whose `claimed_at` is older than `stale_timeout_minutes` back to `PENDING`, incrementing `attempt_count`. This handles crashed workers without human intervention.

### Destination table schema

Auto-created from `pipeline.yaml` on first Run. Includes declared columns plus provenance columns `_source_file_hash TEXT NOT NULL` and `_ingested_at TEXT NOT NULL`. After creation, any mismatch between the YAML and live table columns causes the Run to abort with a diff. No auto-migration is performed.

### Database portability

`etl.db.Database` wraps the connection and normalises `?` parameter placeholders to `%s` for PostgreSQL. All SQL is written with `?` and rewritten at query time. Tests use SQLite; production targets PostgreSQL. DDL is dialect-aware (separate SQLite/PostgreSQL variants selected at connection time).

### CLI interface

```
filedge run   --dir <watched-dir> --config <pipeline.yaml> --db-url <url>
filedge status --db-url <url> [--json]
```

`ETL_DB_URL` environment variable accepted as alternative to `--db-url`.

## Testing Decisions

### What makes a good test

Tests should assert external behavior observable through the module's public interface, not implementation details. A good test for `etl.transform` asserts that a row with a missing required column raises `TransformError` — not that a specific internal variable was set. A good test for `etl.db` asserts that calling `claim_processing` then `mark_committed` produces a `COMMITTED` record — not which SQL statement was executed.

Tests use SQLite (via `tmp_path` pytest fixtures) for all database-touching modules. No mocking of the database layer — the SQLite integration is the test.

### Modules under test

- **`etl.hashing`** — Verify hash stability (same file → same hash), content sensitivity (different content → different hash), and filename independence (same content, different name → same hash).

- **`etl.config`** — Verify `load_config` correctly parses all field types (format, dest_table, columns, retry_cap, stale_timeout_minutes, batch_size), applies defaults for optional fields, and propagates `required: false` correctly.

- **`etl.db`** — Verify the full audit state machine: `insert_pending` → `claim_processing` → `mark_committed`; `insert_pending` → `claim_processing` → `mark_failed`; idempotency (duplicate hash insert is a no-op); stale lock reclaim; `get_status_summary` counts; `ensure_destination_table` creates the table with provenance columns; mismatch detection raises on schema drift.

- **`etl.parser`** — Verify `CSVParser` yields correct dicts from a CSV fixture; `NDJSONParser` yields correct dicts from an NDJSON fixture; both skip blank lines / handle headers correctly; `get_parser` raises on unknown format.

- **`etl.transform`** — Verify type coercions for all supported types (string, integer, float, date, timestamp, boolean); `TransformError` on missing required column; `TransformError` on bad type (e.g. `"abc"` as integer); extra columns silently dropped; optional missing column produces `None` in output.

- **`etl.loader`** — Integration test with SQLite fixture and a sample `PipelineConfig`. Verify: all rows from a CSV file land in the destination table within the transaction; provenance columns are populated; a file with a bad row returns an error string and leaves no rows in the table after rollback; batch boundary (file with rows > batch_size) loads correctly.

## Out of Scope

- Multi-worker concurrency (single ingestion worker process only for now).
- Lenient mode / dead-letter quarantine for bad rows (strict mode only).
- Auto-migration of destination table schema after initial creation.
- Web UI for operator observability (`filedge status` CLI only).
- Non-CSV/NDJSON file formats (Parquet, Avro, etc.) — parser interface is pluggable but not implemented.
- Upsert / replace semantics (append-only load only).
- S3, GCS, or other remote storage as source (local filesystem only).
- Filesystem event-driven discovery (polling only).
- DuckDB or any non-transactional destination store.

## Further Notes

- The three ADRs (`docs/adr/`) record the non-obvious architectural constraints: single-transaction commit, content hash as identity, and strict mode. Any implementation that deviates from these should update the corresponding ADR before proceeding.
- The domain glossary in `CONTEXT.md` defines the canonical terms (File, Content Hash, Commit, Run, Strict Mode, etc.). Use these terms in code identifiers, error messages, and commit messages.
- The system is designed to start small but the deep module interfaces are stable enough that adding Parquet support, multi-worker concurrency, or a web status UI are additive changes that do not require rearchitecting.
