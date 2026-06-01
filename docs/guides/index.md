# How-to guides

Task-focused recipes for getting a specific job done. If you're brand new, run a
[Tutorial](../tutorials/index.md) first; if you're looking up a config option,
jump to the [Reference](../reference/pipeline-yaml.md).

## Build a pipeline

Turn a sample file into a validated `pipeline.yaml`.

- [Author a pipeline](author.md) — interactive terminal UI: infer, review, choose write mode and connector, validate
- [Inspect a file](inspect.md) — sample a file and generate a `columns:` block
- [Preview a file](preview.md) — view rows as a table, jump to any row
- [Validate a file](validate.md) — dry-run a file against a config before writing data

## Run & operate

Ingest files and keep the pipeline healthy in production.

- [Run a pipeline](run.md) — retry-safe commits, write modes, scheduling
- [Scale ingestion](scale.md) — large files, many files, parallel workers, backfills
- [Compact small files](compact.md) — merge many small files into fewer large ones
- [Requeue failed files](requeue.md) — move terminal failures back to `PENDING` after remediation
- [Quarantine bad rows](quarantine.md) — let good rows land, set bad rows aside, investigate and re-drop them
- [Healthcheck](healthcheck.md) — probe the audit DB and destination without writing rows
- [Observability](observability.md) — logs, metrics, and tracing
- [Deploy Filedge](deploy.md) — container image, docker-compose, and Kubernetes CronJobs
- [Export an audit site](audit-export.md) — generate a read-only HTML site for audit stakeholders

## Connect sources

Bring upstream systems in through the same File contract.

- [API sources](api-sources.md) — the Fetcher pattern and the `filedge-fetch` reference companion
- [Stripe API to DuckDB](stripe-duckdb-demo.md) — local golden path for API Source → File → audited load
- [API Source adapters](api-source-adapters.md) — extend the Fetcher to a new API
- [Queue sources](queue-sources.md) — the Materializer pattern and the `filedge-materialize` reference companion
- [Source manifests](source-manifests.md) — upstream lineage for API / Queue / SFTP / vendor exports
- [CDC files](cdc-files.md) — apply change-data-capture files as SCD Type 1 changes

## File formats

- [Overview](file-formats.md) — CSV, NDJSON, Parquet, Excel, and fixed-width at a glance
- [Fixed-width files](fixed-width.md) — ingest fixed-width text with a declared column layout
