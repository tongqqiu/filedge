# ADR-0004: Separate Audit DB from Destination Connector

**Status:** Accepted

## Context

The MVP colocated the audit table and the destination table in the same database. This made the single-transaction Commit (ADR-0001) possible: rows and the COMMITTED marker landed in one `db.commit()` call, so a mid-run crash left nothing behind.

The goal of supporting multiple destination backends (BigQuery, Databricks, PostgreSQL, etc.) breaks this assumption. Cloud data warehouses have no shared transaction scope with a PostgreSQL audit DB — you cannot atomically commit a BigQuery insert and a PostgreSQL UPDATE in one operation.

## Decision

Split the audit DB from the Destination:

- The **Audit DB** is always a local relational database (SQLite or PostgreSQL). It owns the state machine and is the control plane.
- A **Connector** owns all interactions with the Destination — DDL, row writes, idempotency. It is instantiated from `pipeline.yaml` (`connector.type`) and is completely decoupled from the Audit DB.

The single-transaction Commit guarantee (ADR-0001) weakens to a **two-phase commit**:
1. Connector writes rows to the Destination.
2. Pipeline marks the file COMMITTED in the Audit DB.

A crash between phases 1 and 2 leaves the file in PROCESSING. The stale-lock reclaim mechanism resets it to PENDING on the next Run, and the Connector is retried.

## Consequence: Connector-Level Idempotency

Because the two-phase window means a Connector write may be retried, every Connector must make `write_rows` idempotent per `file_hash`:

- **postgres**: `DELETE FROM dest WHERE _source_file_hash = $1` then insert.
- **bigquery**: use `file_hash` as the BigQuery load job ID — BigQuery deduplicates jobs with the same ID.
- **databricks**: `MERGE INTO ... WHEN NOT MATCHED` on `_source_file_hash`.

The `_source_file_hash` provenance column (established in the MVP) is what makes this possible.

## Alternatives Considered

**Keep everything in the same DB.** Preserves the atomic guarantee but rules out BigQuery, Databricks, and any other system that doesn't share a transaction scope with the audit DB. Not viable for the stated goal.

**Accept duplicates and rely on downstream dedup.** Simpler Connector interface, but shifts a reliability concern onto consumers. Rejected: provenance columns make Connector-level idempotency cheap enough that we should own it.
