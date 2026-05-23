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

Files are processed in row batches (default: 1,000 rows) rather than loaded entirely into memory. The wrapping database transaction stays open across all batches and commits only when the full file is processed — preserving atomicity at constant memory cost regardless of file size.

## Schema guard

On first run against a new destination table, the connector creates the table from the `pipeline.yaml` schema. On subsequent runs, if the live table schema doesn't match the config, the run fails loudly with a diff — no silent auto-migration. Schema changes require explicit operator action.

## Design decisions

See [Design decisions](decisions.md) for the ADRs behind these choices.
