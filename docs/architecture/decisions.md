# Design decisions

These ADRs record the non-obvious choices made during development — decisions that would otherwise be rediscovered from scratch by anyone reading the code.

---

## ADR-0001: Single-transaction commit {#adr-0001}

Ingested records and the file-level audit marker are written in a single database transaction. Either both land or neither does.

The alternative — a separate audit store updated after the data write — requires a two-phase commit or saga pattern to be safe, which still leaves a failure window. This constraint means the destination must be a transactional database, which is an acceptable trade-off for a reliability-first system.

**Superseded by:** ADR-0004 (the audit/connector split later revisited this constraint).

[Full ADR](../adr/0001-single-transaction-commit.md)

---

## ADR-0002: Content hash as idempotency key {#adr-0002}

Files are identified by SHA-256 of their bytes, not by filename.

This prevents a specific failure mode: a file re-dropped with the same name but corrected content would be silently skipped if filename were the key. The inverse is also handled: two files with identical content but different names are treated as one load.

The trade-off: two genuinely distinct files with the same content produce one set of rows. That is correct — an audit system should not double-count identical data.

[Full ADR](../adr/0002-content-hash-as-idempotency-key.md)

---

## ADR-0003: Whole-file failure on validation error (strict mode) {#adr-0003}

If any row in a file fails schema validation, the entire file fails — no records are committed.

Lenient mode (commit valid rows, discard bad ones) was rejected because partial commits make destination completeness unverifiable. A table that received 9,800 of 10,000 rows looks identical to one that received all 10,000. `FAILED` is an unambiguous signal: nothing landed, retry is safe, fix the source data.

[Full ADR](../adr/0003-strict-mode-validation.md)

---

## ADR-0004: Separate audit DB from destination connector {#adr-0004}

The audit database (state machine, attempt counts, provenance) and the destination (ingested rows) are separate systems with separate connections and transactions.

This enables pluggable destinations (BigQuery, DuckDB, etc.) that cannot share a transaction with a SQL audit DB. The connector is responsible for making `write_rows` idempotent per `file_hash`; the stale-lock reclaim handles the failure window between the two writes.

[Full ADR](../adr/0004-audit-connector-split.md)

---

## ADR-0005: SFTP out of scope {#adr-0005}

SFTP is not a supported Watched Directory source. Use a dedicated sync layer (rclone, lftp, AWS Transfer Family) to land files in a local directory or cloud bucket first.

Two reasons: SFTP protocol diversity and auth complexity are better handled by purpose-built tools; and implementing reliable incremental SFTP sync is a project in itself that would dominate the codebase without being the core value.

[Full ADR](../adr/0005-sftp-out-of-scope.md)

---

## ADR-0006: API sources materialized as files before ingestion {#adr-0006}

API data (Stripe, Salesforce, HubSpot, etc.) is materialized as NDJSON files by a Fetcher (dlt) before `etl run` ingests them — not loaded directly by dlt.

This preserves a single audit model for all data sources. An operator asking "what Stripe data landed on a given date and which destination rows did it produce?" gets the same answer format as for CSV file drops. For fintech operators, audit uniformity across all sources is non-negotiable.

[Full ADR](../adr/0006-api-sources-fetched-to-files.md)

---

## ADR-0007: Queue source ingestion model {#adr-0007}

Queue sources (Kafka, SQS) use **Drain** as the default trigger mode — snapshot the high-water mark at startup, consume all available micro-batches, then exit. Continuous (long-lived consumer) is supported as an opt-in.

Drain preserves the existing operational model: `etl consume` is scheduled by the same external scheduler as `etl run`, requires no process manager, and crash recovery falls to the existing stale-lock reclaim. The latency trade-off is accepted for batch-oriented fintech ingestion.

[Full ADR](../adr/0007-queue-source-ingestion-model.md)

---

## ADR-0008: Schema inference confidence tiers {#adr-0008}

`etl inspect` annotates each inferred column with a confidence tier (high / low / ambiguous) rather than silently picking the most specific type or defaulting everything to `string`.

Aggressive inference misleads operators when sparse nulls or format exceptions appear beyond the sample window. Conservative inference produces configs full of `string` columns that defeat the tool's purpose. Annotated tiers give operators exactly the signal they need: "this column is fine, that one needs your eyes."

[Full ADR](../adr/0008-schema-inference-confidence-tiers.md)
