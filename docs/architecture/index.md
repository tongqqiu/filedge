# How it works

## The file state machine

Every file passes through four states:

```
PENDING → PROCESSING → COMMITTED
                    → FAILED (→ PENDING on retry)
```

- **PENDING** — discovered but not yet claimed
- **PROCESSING** — claimed by a worker (distributed lock via content hash)
- **COMMITTED** — fully loaded; rows and audit marker landed atomically
- **FAILED** — load attempt failed; eligible for retry up to `retry_cap`

A file whose content hash is already `COMMITTED` is silently deduplicated at the entry point — it is never re-processed.

## Two separate databases

The system separates concerns into two independent stores:

```
                    ┌─────────────────┐
                    │   Audit DB      │
                    │  (SQLite/PG)    │
                    │                 │
                    │ file states     │
                    │ attempt counts  │
  etl run ─────────│ worker identity │
                    └────────┬────────┘
                             │
                             │ separate connection, separate transaction
                             │
                    ┌────────▼────────┐
                    │   Destination   │
                    │ (PG/BQ/DuckDB)  │
                    │                 │
                    │ ingested rows   │
                    │ + provenance    │
                    └─────────────────┘
```

**Audit DB** — the control plane. Always SQLite or PostgreSQL. Drives the state machine, coordinates retries, holds file-level provenance.

**Destination** — where ingested rows land. Any supported connector. Knows nothing about the audit DB.

Because they are separate, a crash between the connector write and the audit mark leaves the file in `PROCESSING`. The stale-lock reclaim on the next run picks it up, and the connector's per-file-hash idempotency ensures no duplicate rows.

## Runtime module responsibilities

`filedge run` is intentionally thin orchestration around a few deeper modules:

```
Pipeline Config
├── config: parse YAML and validate destination identifiers
├── identifiers: validate/quote table and column names
├── schema: build expected destination schema and compare live tables
├── db: own File lifecycle in the Audit DB
├── load_stream: parse, transform, count rows, and report row-numbered errors
└── connector: own Destination DDL, idempotent writes, and provenance columns
```

This keeps the reliability rules close to the data they protect:

- **Audit DB lifecycle** lives in `filedge.db`: run preparation, File discovery, claiming, and finishing.
- **Strict Mode loading** lives in `filedge.load_stream`: a parse or transform failure includes row context and fails the whole File.
- **Destination schema guard** lives behind `filedge.schema` plus Connector adapters: every Connector compares the live table against the same Pipeline Config contract.
- **SQL identifier handling** lives in `filedge.identifiers`: destination table and column names are validated early and quoted consistently by SQL Connectors.
- **Connector idempotency** lives in each Connector: retries with the same Content Hash produce the same Destination state.

The result is that `filedge.pipeline` coordinates the Run but does not own the File state machine, row validation rules, or Destination SQL details.

## Run lifecycle

A Run proceeds in four phases:

```
Run N
├── Audit DB: prepare_run
│   ├── reset retryable FAILED files → PENDING
│   └── reclaim stale PROCESSING locks → PENDING
├── Connector: ensure destination table exists
│   ├── validate destination identifiers
│   └── create or compare schema from pipeline.yaml
├── Watched Directory: hash files and discover new Content Hashes
└── For each claimable File:
    ├── Audit DB: claim_pending_file → PROCESSING
    ├── load_stream: parse + transform rows with Strict Mode
    ├── Connector: write_rows idempotently with provenance
    └── Audit DB: finish_file → COMMITTED or FAILED
```

Files already known to the Audit DB are not re-enqueued. Terminal `FAILED` files remain skipped until an operator resets them.

## Atomicity within each system

**Audit DB** — state transitions are SQL transactions. Marking a file `COMMITTED` or `FAILED` is an atomic operation.

**Connector** — `write_rows` is idempotent per `file_hash`. If the process crashes mid-write, the next call with the same `file_hash` produces the same final destination state (either delete-then-insert for append mode, or truncate-then-insert for truncate mode).

The two transactions cannot be made truly atomic (they are different databases), but the combination of stale-lock recovery and connector idempotency makes the system **effectively exactly-once** in practice.

## Content-based identity

Files are identified by SHA-256 of their bytes, not by filename. Two files with the same content hash are treated as identical. This means:

- Renaming a file and re-dropping it → skipped (same hash already `COMMITTED`)
- Replacing a file with different content → ingested as a new file (different hash)
- Copying a file to a new name → skipped (same hash)

## Streaming load

Files are processed as a stream rather than loaded entirely into memory. `load_stream` opens the File with the configured Parser, transforms each row according to Pipeline Config, counts rows, and attaches row numbers to parse or transform failures. Connectors flush rows in batches (default: 1,000 rows) while keeping their Destination write idempotent for the Content Hash.

If any row fails parsing or transformation, Strict Mode marks the File `FAILED`; no partial File is considered committed.

## Schema guard

On first run against a new destination table, the connector creates the table from the `pipeline.yaml` schema. On subsequent runs, if the live table schema doesn't match the config, the run fails loudly with a diff — including missing columns, unexpected live columns, and type mismatches. No silent auto-migration is performed; schema changes require explicit operator action.

Destination table and column identifiers are validated before table setup. SQL Connectors quote accepted identifiers consistently, so ordinary names and reserved-word-like names are handled predictably, while unsupported names fail early with operator-facing errors.

## Design decisions

See [Design decisions](decisions.md) for the ADRs behind these choices.
