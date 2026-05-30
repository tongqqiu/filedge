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

API data (Stripe, Salesforce, HubSpot, etc.) is materialized as complete NDJSON files by an upstream Fetcher before `filedge run` ingests it. dlt can be one such Fetcher, but it is not a Filedge dependency and does not load directly to the destination in the Filedge model.

This preserves a single audit model for all data sources: Filedge starts at the File boundary, then applies Content Hash deduplication, strict validation, row-level provenance, and the same audit state machine. For fintech operators, audit uniformity across all sources is non-negotiable.

[Full ADR](../adr/0006-api-sources-fetched-to-files.md)

---

## ADR-0007: Queue sources materialized as files before ingestion {#adr-0007}

Queue data (Kafka, SQS, Kinesis, etc.) is materialized as complete NDJSON or Parquet files by an upstream Queue Materializer before `filedge run` ingests it. Kafka Connect, Flink, Spark, Vector, Benthos, cloud delivery services, and custom consumers can all play this role.

This keeps Filedge's boundary consistent with SFTP and API sources: external tools handle transport-specific complexity, then Filedge applies Content Hash deduplication, strict validation, row-level provenance, retry behavior, and the same audit state machine to complete Files.

[Full ADR](../adr/0007-queue-source-ingestion-model.md)

---

## ADR-0008: Schema inference confidence tiers {#adr-0008}

`filedge inspect` annotates each inferred column with a confidence tier (high / low / ambiguous) rather than silently picking the most specific type or defaulting everything to `string`.

Aggressive inference misleads operators when sparse nulls or format exceptions appear beyond the sample window. Conservative inference produces configs full of `string` columns that defeat the tool's purpose. Annotated tiers give operators exactly the signal they need: "this column is fine, that one needs your eyes."

[Full ADR](../adr/0008-schema-inference-confidence-tiers.md)

---

## ADR-0009: Warehouse CDC uses Destination-side Applied File Markers {#adr-0009}

Warehouse CDC Connectors use a Destination-side Applied File Marker keyed by destination table and Content Hash to make retries safe across the Audit DB / Destination failure window.

This is needed because replaying a CDC File re-applies business-key mutations, which cannot be made safe by row-level `_source_file_hash` alone. The Applied File Marker complements the Audit DB; it does not replace the Audit Record.

[Full ADR](../adr/0009-warehouse-cdc-applied-file-markers.md)

---

## ADR-0010: Audit Export is a read-only static site {#adr-0010}

`filedge export-audit` renders Audit DB records into a self-contained HTML file for compliance stakeholders who need read-only evidence without database or CLI access.

The export deliberately excludes destination row data. It shows file-level audit state and copyable lineage SQL keyed by `_source_file_hash`, keeping sensitive row data inside the warehouse and leaving authentication to the static hosting layer.

[Full ADR](../adr/0010-audit-export-static-site.md)

---

## ADR-0011: Source Manifest is an OpenLineage-shaped sidecar {#adr-0011}

Upstream Fetchers, Queue Materializers, SFTP sync jobs, and vendor export processes may write `<data-file>.manifest.json` sidecars next to complete Files. Filedge reads a small OpenLineage-shaped subset, stores the raw manifest on the Audit Record, and surfaces it through `filedge lineage` and `status --json`.

Filedge consumes this shape but does not emit to lineage backends or take over source mechanics. That keeps the File boundary intact while giving regulated pipelines a deterministic link back to upstream source ranges.

[Full ADR](../adr/0011-source-manifest-and-lineage.md)

---

## ADR-0012: Excel is the next Parser format, ahead of Avro {#adr-0012}

Excel (`.xlsx`) ships as the next Parser format because real target users — fintech data teams — land small datasets as spreadsheets, while Avro use is already covered by the Queue Materializer pattern. Schema inference, preview, and validate all work on `.xlsx` via `openpyxl` (optional `excel` extra), with row 1 as the header and a `--sheet` selector for multi-sheet workbooks.

This re-applies the principle from ADR-0005/0006/0007: format priority follows real target-user evidence, not an abstract roadmap.

[Full ADR](../adr/0012-excel-format-support.md)

---

## ADR-0013: Fixed-width is a Parser format with schema declared in pipeline.yaml {#adr-0013}

Fixed-width text files have no separator and no embedded schema, so the column layout (`start`/`width` per column) is declared inline in `pipeline.yaml` from the partner's record-layout spec. `filedge inspect` is unsupported for fixed-width — inference is infeasible without an external layout — and `preview`/`validate` require `--config`. Validation rejects overlapping, unsorted, or non-positive-width columns at load time.

[Full ADR](../adr/0013-fixed-width-format-support.md)

---

## ADR-0014: Column-level Field Encryption {#adr-0014}

A per-column Field Encryption step runs between Transform and Connector so plaintext PII never lands in the warehouse. An `encrypt:` block (AES-256-GCM, randomized) gives confidentiality; a `hash:` block (HMAC-SHA256) gives a one-way joinable token; a column may declare neither, one, or both. Filedge owns the crypto math but not key management — key material comes from environment variables or a secrets mount at runtime, never from YAML, and Filedge does not integrate with KMS.

[Full ADR](../adr/0014-column-level-field-encryption.md)

---

## ADR-0015: Control and Audit Platform starts with local Pipeline Authoring {#adr-0015}

Filedge's first platform surface is a local Pipeline Authoring UI, not a hosted read-write operations platform. The Authoring UI helps operators create and review Pipeline Configs with preview, Schema Inference, Authoring Validation, connector settings, and Credential Placeholders; it does not run ingestion, store secrets, mutate Audit Records, or become a second control plane. A Pipeline Registry is created with the first authored Pipeline and keeps Audit DBs independent — preserving the one-Audit-DB-per-Pipeline rule.

[Full ADR](../adr/0015-control-and-audit-platform-starts-with-local-pipeline-authoring.md)

---

## ADR-0016: Authoring UI is a Textual TUI launched from the Operator CLI {#adr-0016}

The Authoring UI is a [Textual](https://textual.textualize.io) terminal app launched via `filedge author <sample-file>`, shipped behind an optional `authoring` extra. A terminal app is the most CLI-adjacent choice (same shell, working directory, and environment as the Operator CLI), is testable in-process via Textual's `Pilot` harness, and adds a lightweight dependency surface. The shell only renders panes and routes keystrokes; every domain rule stays in deep `filedge.*` modules reused unchanged from the CLI.

[Full ADR](../adr/0016-authoring-ui-textual-tui.md)

---

## ADR-0017: Pipeline Folder layout and Pipeline Registry format {#adr-0017}

Authored artifacts land in visible, version-controllable files at the workspace root: a `pipelines/<id>/` directory per Pipeline (holding `pipeline.yaml` and a Markdown Authoring Runbook) and a single `pipeline-registry.yaml` index. The Registry is created lazily with the first authored Pipeline and grows one independent entry per Pipeline, each pointing at its Pipeline Folder, Watched Directory, Audit DB connection placeholder, and Audit Export destination. It never combines Audit DBs and rejects malformed entries.

[Full ADR](../adr/0017-pipeline-folder-and-registry-layout.md)

---

## ADR-0018: The Reference Fetcher is an external companion, not core {#adr-0018}

Filedge ships a Reference Fetcher (`filedge-fetch`) as a runnable example of the external Fetcher role from ADR-0006, without reopening that boundary. It is a separate console script (not a `filedge` subcommand), the core ingestion path imports nothing from it, and it is never a loader of record — `filedge run` still owns every Destination Commit. It stages a complete NDJSON File, emits the OpenLineage-shaped Source Manifest the reader already consumes (ADR-0011), and promotes the sidecar then the data File into the Watched Directory under a Fetch Lock, advancing the incremental cursor only after promotion. The reference targets one open, no-auth API behind a source-client seam so a fintech API is later config, not a rewrite.

[Full ADR](../adr/0018-reference-fetcher-external-companion.md)
