# ETL Big Idea

A batch ETL toolbox for data engineers who need reliable file ingestion — with atomic commits, content-based idempotency, and a full audit trail built in.

---

## The Problem

Most ETL pipelines fail in the worst possible way: they write half the rows before crashing, leaving the destination in a corrupt state with no record of what happened. Re-running the job then double-writes the rows that did succeed.

ETL Big Idea addresses three root causes:

- **Partial load corruption** — rows and the audit marker are written atomically. Either both land or neither does.
- **Filename-based idempotency** — files are identified by SHA-256 content hash, not filename. Renaming a file doesn't re-ingest it; replacing it with new content does.
- **No audit trail** — every file passes through a `PENDING → PROCESSING → COMMITTED/FAILED` state machine stored alongside row-level provenance.

---

## The Toolbox

Four CLI commands, each useful on its own:

| Command | What it does |
|---------|-------------|
| `etl inspect <file>` | Sample a file and generate a `pipeline.yaml` columns block |
| `etl validate <file>` | Dry-run a file against a config — no data written |
| `etl compact` | Merge many small files into fewer large ones before ingestion |
| `etl run` | Ingest files with atomic commits, retry, and full audit trail |

The typical workflow for a new pipeline:

```
etl inspect data.csv > pipeline.yaml   # 1. generate config
etl validate data.csv --config pipeline.yaml  # 2. check it
etl run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///etl.db  # 3. run it
```

---

## Destinations

Pluggable via a `connector:` block in `pipeline.yaml`:

- **SQLite** — local dev and lightweight deployments
- **PostgreSQL** — production workloads
- **BigQuery** — GCP data warehouses
- **Databricks** — Databricks SQL warehouses
- **DuckDB** — local analytics

---

## Quick Links

- [Getting Started](getting-started.md) — install and run your first pipeline in 5 minutes
- [Guides](guides/inspect.md) — one page per tool
- [pipeline.yaml reference](reference/pipeline-yaml.md) — every config option
- [Connectors](reference/connectors.md) — destination setup for each backend
