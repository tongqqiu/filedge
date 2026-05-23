# Context: Filedge

A batch ETL system designed for reliable data ingestion from files, APIs, and message queues, targeting the failure modes that Airflow + Spark + data warehouse stacks handle poorly.

---

## Glossary

### File
The atomic unit of work. A single raw input file must either be fully loaded into the destination or not at all — partial states are not permitted.

### Content Hash
The primary idempotency key for a File. Computed as SHA-256 of the file's bytes. Two files with the same Content Hash are treated as identical data regardless of filename. Stored alongside the filename in the audit record.

### Partial Load Corruption
The #1 failure mode this system is designed to prevent. Occurs when a pipeline job fails mid-run and leaves the destination in a half-written state, causing subsequent retries to produce duplicates or skip records.

### Commit
The act of writing a File's records and its audit marker together in a single database transaction. Either both land or neither does. This is what makes a File ingestion atomic.

### Run
A single execution of `filedge run` — a short-lived process that scans the Watched Directory, enqueues new Files as PENDING, processes them through the pipeline, and exits. Triggered by an external scheduler (cron, Airflow, Kubernetes CronJob). Stale PROCESSING locks older than a configured timeout are reclaimed at the start of each Run.

### Streaming Load
Files are processed in row batches (configurable size, default 1,000) rather than loaded entirely into memory. The wrapping database transaction stays open across all batches and commits only when the full file is processed — preserving atomicity at constant memory cost regardless of file size.

### Append-Only Load
The default Write Mode (`write_mode: append`): records from each File are inserted into the destination table without replacing prior records. The ETL layer does not resolve whether a re-dropped file is a correction or a supplement — that is downstream responsibility, resolvable via provenance columns. Two Files with the same filename but different content hashes produce two distinct sets of rows in the destination. See also: Write Mode.

### Column Tolerance
Extra columns in a source file (not declared in Pipeline Config) are silently ignored. Missing columns declared as required in Pipeline Config cause the File to fail in Strict Mode. This asymmetry makes the pipeline tolerant of upstream additions while strict about data contract violations.

### Table Initialization
On first Run against a new destination table, the system creates the table from the Pipeline Config schema (including provenance columns). After the table exists, any mismatch between the YAML and the live table causes the Run to fail loudly with a clear diff — no auto-migration. Schema changes require explicit operator action.

### Runtime
Python. The implementation language for the ingestion system, CLI, and all pipeline components.

### Operator CLI
A command-line interface for system observation and control. `filedge status` prints file counts by state, recent failures, and retry counts. Supports `--json` for machine-readable output. `etl inspect <file>` runs Schema Inference on a file and prints a suggested `columns:` block. The stable interface over audit DB queries — future web UI would use the same backing queries.

### Schema Inference
The process of sampling the first N rows of a File (default 1,000, configurable via `--sample-rows`) and producing a suggested `columns:` block ready to paste into a Pipeline Config, alongside a human-readable summary. Each inferred column carries a Confidence Tier. Invoked via `etl inspect <file>`. Format is auto-detected from file extension with a `--format` override. The YAML block goes to stdout; the summary goes to stderr, keeping them composable with shell redirection. NDJSON nested objects are surfaced as top-level `string` columns with a warning listing the nested keys — the pipeline has no flattening Transform, so suggesting dot-notation paths would produce a config that cannot be executed.
_Avoid_: schema detection, type inference, column discovery.

### Confidence Tier
An annotation attached to each column in Schema Inference output, expressing how strongly the evidence supports the inferred type and `required:` value. Three tiers: **high** (all sampled values parse cleanly, no nulls); **low** (most values parse but exceptions found — null count or unparseable values shown); **ambiguous** (evidence is genuinely conflicting — e.g. two date formats detected, or values that could be boolean or integer). Operators are expected to review low and ambiguous columns before committing the config to production.
_Avoid_: confidence score, inference quality, certainty level.

### Pipeline Config
A `pipeline.yaml` file that declares how a single ingestion pipeline behaves. Contains a `source:` block (type-specific config for a Watched Directory or Queue Source), file format, column mappings (source name → destination name + type), destination table name, and retry cap. The operator interface for configuring ingestion — no code changes required for schema mapping updates or source type changes.

### Audit Record
Two-level audit: (1) file-level — captures filename, content hash, state, attempt count, timestamps, and worker identity; (2) row-level provenance — every destination row carries `_source_file_hash` and `_ingested_at` columns linking it back to its source File. Row-level provenance is non-negotiable: it is the basis for data lineage, debugging, and compliance.

### Audit DB
The relational database (SQLite for development, PostgreSQL for production) that holds the file-level audit records and drives the state machine (PENDING → PROCESSING → COMMITTED/FAILED). This is the control plane — it is always a SQL database with full transaction support, separate from the Destination.

### Connector
A pluggable adapter that owns all interactions with a specific Destination backend: creating or validating the destination table, writing rows, and enforcing write-mode semantics. The Connector is the only component that knows about the Destination's SDK, DDL dialect, and bulk-load API. Adding a new Destination means writing a new Connector — no changes to the pipeline or audit logic. Built-in Connectors: `sqlite`, `postgres`, `bigquery`, `databricks`, `duckdb`.

### BigQuery Connector
A Connector that writes rows to a BigQuery table via NDJSON staging and a bulk load job. Idempotency in append mode is achieved by encoding the `file_hash` in the BigQuery job ID: if a job with the same ID already exists and succeeded, the retry is a no-op. **Known limitation**: BigQuery only retains job metadata for 7 days. A retry of the same file more than 7 days after the original ingestion will submit a new job and produce duplicate rows. For pipelines where files may be re-ingested after this window, use `write_mode: truncate` or implement a pre-load DML DELETE.

### DuckDB Connector
A Connector that writes rows to a `.duckdb` file on disk. Targeted at local analytics and lightweight deployments where a full OLAP warehouse (BigQuery, Databricks) is overkill. DuckDB is file-based and supports only one writer at a time — the Connector fails fast with a clear error if the file is locked by another process rather than retrying. DuckDB is a destination only; the audit DB always remains SQLite or PostgreSQL. Rows are written via standard batched `executemany`; bulk Parquet loading is a future optimization.

### Destination
The system where ingested rows land. Decoupled from the Audit DB — each has its own connection and transaction scope. Because rows and the audit COMMITTED marker can no longer be written in a single transaction, the Connector is responsible for making `write_rows` idempotent per `file_hash`, so retries produce the same destination state as a first write.

### Write Mode
The strategy a Connector uses when writing a file's rows to the destination table. Declared as `write_mode` in `pipeline.yaml`. Two modes are supported: `append` (default) — rows are added alongside prior records, idempotent via delete-where-hash then insert; `truncate` — the table is wiped then replaced with this file's rows, naturally idempotent. A third mode, `merge` (upsert by business key), is deferred.

### Connector Registry
The internal mapping from a `connector.type` string (e.g. `bigquery`) to a Connector implementation class. Resolved lazily at instantiation time so that missing optional SDK dependencies surface as a clear error only when the Connector is actually used. Declared in `pipeline.yaml` under a `connector:` block; secrets (API tokens, service account credentials) come from environment variables, never from YAML.

### Retry
Automatic re-attempt of a FAILED File with exponential backoff, up to a configured max attempt count (default: 3). After the cap is reached, the File enters terminal FAILED state requiring explicit human re-queue (resetting state to PENDING). Prevents bad files from burning retries indefinitely.

### Strict Mode
The validation policy for a File load: if any row fails schema validation, the entire File fails — no records are committed. This preserves the ability to reason about completeness. Lenient partial commits are not supported; a dead-letter quarantine is a future addition.

### Transform
A declarative, configuration-driven step that maps source column names to destination column names and coerces types (e.g. string → integer, ISO string → timestamp). Rejects rows that don't conform to the declared schema. No business logic — that belongs in the application layer consuming the destination.

### Compaction
A pre-processing step that merges many small Files in a source prefix into fewer, larger NDJSON files in a separate output prefix before ingestion. Solves the small-files problem common with event streams and cloud object stores — reducing object-store listing cost and enabling bulk loads into cloud warehouses. Invoked via `filedge compact` as a separate CLI command, scheduled before `filedge run`. Compaction reads via fsspec (no extra dependencies), groups files by count (`--max-files`), writes NDJSON with optional gzip compression (`--compress`), and names output files by timestamp and batch index. Originals in the source prefix are never modified. The output prefix becomes the Watched Directory for the subsequent `filedge run`.

### Parser
A pluggable component that takes a File path and yields rows. Implementations exist for CSV and newline-delimited JSON. Format is detected by file extension or per-directory configuration. Adding new formats (Parquet, Avro) is a new Parser implementation, not a system redesign.

### Watched Directory
The landing zone polled on a schedule to discover new Files. Accepts a local path or a cloud URI (`gs://`, `s3://`). The system scans the location on every Run, computes content hashes, filters out already-COMMITTED files, and enqueues new ones as PENDING. The Watched Directory is assumed to contain only complete, transfer-ready files — partial transfers and in-flight writes are the responsibility of whatever process deposits files there. SFTP is not a supported source; see ADR-0005.

For large-scale deployments where object-store listing cost or latency becomes a concern, operators should use time-partitioned prefixes — e.g. `s3://bucket/landing/2026-05-23/` — and update the `--watched-dir` argument daily. This keeps each Run's listing bounded to that day's files without requiring the pipeline to move or delete objects after ingestion.

### File States
The four states a File passes through: `PENDING` (discovered, not yet claimed), `PROCESSING` (claimed by a worker — acts as a distributed lock via content hash), `COMMITTED` (fully loaded, transaction complete), `FAILED` (load attempt failed, eligible for retry or human review). A file whose content hash is already `COMMITTED` is never admitted to the pipeline — it is silently deduplicated at the entry point.

### Target User
Data engineering teams at fintech companies where file ingestion is business-critical and high-visibility auditability is a compliance requirement. Every file must be traceable from source to destination row, and the audit trail must be uniform across all data sources — whether data arrives as file drops or via API.
_Avoid_: General data engineering teams, analytics teams.

### API Source
A data source that delivers records via HTTP API rather than file drops. Examples: Stripe, Salesforce, HubSpot, Jira, GitHub. API Sources are not polled directly by the pipeline — they require a Fetcher to materialize their data as NDJSON files before ingestion.
_Avoid_: API connector, API pipeline.

### Fetcher
A component that pulls data from an API Source on a schedule, handles pagination, authentication, rate limiting, and incremental cursor management, and writes complete NDJSON files to the Watched Directory. The Fetcher is the API-source equivalent of the rclone sync layer for SFTP (ADR-0005). dlt (dlthub.com) is the recommended Fetcher implementation — it provides 300+ pre-built API Source connectors. Invoked via `etl fetch --config sources.yaml --output <watched-dir>`. Only complete files reach the Watched Directory; dlt writes to a staging prefix first, files are promoted on success and deleted on failure.
_Avoid_: API connector, extractor, source connector.

### Fetch Lock
A `.fetch.lock` file written to the staging prefix at the start of `filedge fetch` and deleted on completion (success or failure). Prevents concurrent fetches of the same API Source from racing to promote partial files to the Watched Directory. A fresh lock causes `filedge fetch` to fail fast; a stale lock (older than a configurable TTL) is reclaimed and overwritten. The lock is a filesystem artifact, not an Audit DB record — `filedge fetch` has no dependency on the Audit DB.
_Avoid_: fetch mutex, distributed lock.

### Decoder
A pluggable component that takes a single queue message payload (bytes) and returns one row (dict). The queue-source equivalent of a Parser. Declared as `format:` in the `source:` block of Pipeline Config. Current implementation: `json` (parses UTF-8 bytes as a JSON object). Avro and Protobuf are future Decoder implementations — not supported until Schema Registry integration is designed.
_Avoid_: deserializer, message parser, codec.

### Queue Source
A data source that delivers records continuously via a message broker (Kafka, SQS, Kinesis). Unlike a Watched Directory or API Source, a Queue Source has no natural file boundary — records arrive individually and must be grouped into Micro-batches before ingestion. Configured via a `source:` block in Pipeline Config with broker addresses, topic, consumer group, and Trigger Mode.
_Avoid_: streaming source, event source, message queue.

### Micro-batch
The unit of work for Queue Source ingestion. A group of records accumulated from a Queue Source until either a record count limit or a time window elapses — whichever comes first. Plays the same role as a File in the file ingestion model: it has an Offset Range Key, passes through the same state machine (PENDING → PROCESSING → COMMITTED/FAILED), and either commits fully or not at all. The Kafka consumer offset is advanced only after the Micro-batch commits to the destination — guaranteeing at-least-once delivery with effective exactly-once via Offset Range Key deduplication.
_Avoid_: batch, mini-batch, window.

### Offset Range Key
The idempotency key for a Micro-batch. Structured as `{topic}:{partition}:{start_offset}:{end_offset}`. Stable across retries: re-consuming the same offset range produces the same key, enabling deduplication at the Audit DB entry point. Plays the same role as Content Hash does for Files.
_Avoid_: batch key, offset hash, consumer checkpoint.

### Trigger Mode
The policy that governs when a queue consumer stops consuming and exits. Declared as `trigger:` in the `source:` block of Pipeline Config. Two modes: Drain and Continuous.

### Drain
A Trigger Mode. At startup, the consumer snapshots the current high-water mark offset per partition. It consumes all available Micro-batches up to that offset, then exits. Messages that arrive after the snapshot are left for the next invocation. Scheduled via an external scheduler (cron, Kubernetes CronJob) — the same operational model as `filedge run`. Analogous to Spark's `Trigger.AvailableNow()`. This is the default Trigger Mode.
_Avoid_: trigger once, batch mode, bounded consume.

### Continuous
A Trigger Mode. The consumer runs indefinitely, cutting a new Micro-batch every N records or T seconds. Stopped by SIGTERM; the in-flight Micro-batch is allowed to finish before the process exits. Used when queue latency requirements cannot be met by a scheduled Drain. Requires a process manager (Kubernetes Deployment, systemd) rather than a cron scheduler.
_Avoid_: streaming mode, always-on, daemon.

### Sources Config
A `sources.yaml` file that declares an API Source for `filedge fetch`: the dlt source type, which endpoints to pull, the incremental key, and the staging prefix. One file per API Source. Credentials never appear in the file — they are read from environment variables by dlt. Analogous to `pipeline.yaml` for the ingestion side.
_Avoid_: fetch config, source pipeline.
