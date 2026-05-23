# Context: ETL Big Idea

A batch ETL system designed for reliable file ingestion, targeting the failure modes that Airflow + Spark + data warehouse stacks handle poorly.

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
A single execution of `etl run` — a short-lived process that scans the Watched Directory, enqueues new Files as PENDING, processes them through the pipeline, and exits. Triggered by an external scheduler (cron, Airflow, Kubernetes CronJob). Stale PROCESSING locks older than a configured timeout are reclaimed at the start of each Run.

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
A command-line interface for system observation and control. `etl status` prints file counts by state, recent failures, and retry counts. Supports `--json` for machine-readable output. The stable interface over audit DB queries — future web UI would use the same backing queries.

### Pipeline Config
A `pipeline.yaml` file co-located with each Watched Directory. Declares: file format, column mappings (source name → destination name + type), destination table name, and retry cap. The operator interface for configuring ingestion — no code changes required for schema mapping updates.

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

### Parser
A pluggable component that takes a File path and yields rows. Implementations exist for CSV and newline-delimited JSON. Format is detected by file extension or per-directory configuration. Adding new formats (Parquet, Avro) is a new Parser implementation, not a system redesign.

### Watched Directory
The source location polled on a schedule to discover new Files. The system scans the directory, computes content hashes, filters out already-COMMITTED files, and enqueues new ones as PENDING. Poll interval is configuration.

### File States
The four states a File passes through: `PENDING` (discovered, not yet claimed), `PROCESSING` (claimed by a worker — acts as a distributed lock via content hash), `COMMITTED` (fully loaded, transaction complete), `FAILED` (load attempt failed, eligible for retry or human review). A file whose content hash is already `COMMITTED` is never admitted to the pipeline — it is silently deduplicated at the entry point.
