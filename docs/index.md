# Filedge

A batch ETL system for data engineers who need reliable file ingestion: per-file destination commits, content-hash idempotency, crash-safe retry, and a full audit trail.

---

## The Problem

Most ETL pipelines fail in the worst possible way: they write half the rows before crashing, leaving the destination in a corrupt state with no clear record of what happened. Re-running the job then risks double-writing the rows that did succeed.

Filedge addresses three root causes:

- **Partial load corruption** — each destination connector makes a file write retry-safe for the file's content hash.
- **Filename-based idempotency** — files are identified by SHA-256 content hash, not filename. Renaming a file doesn't re-ingest it; replacing it with new content does.
- **No audit trail** — every file passes through a `PENDING → PROCESSING → COMMITTED/FAILED` state machine stored alongside row-level provenance.

---

## The Toolbox

Core CLI commands, each useful on its own:

| Command | What it does |
|---------|-------------|
| `filedge inspect <file>` | Sample a file and generate a `pipeline.yaml` columns block |
| `filedge preview <file>` | Display rows as a table — jump to any row with `--start-row` |
| `filedge validate <file>` | Dry-run a file against a config — no data written |
| `filedge compact` | Merge many small files into fewer large ones before ingestion |
| `filedge run` | Ingest files with retry-safe commits and a full audit trail |
| `filedge status` | Show counts and recent failures from the audit DB |
| `filedge export-audit` | Generate a read-only static HTML site for compliance and audit stakeholders |
| `filedge healthcheck` | Probe the audit DB and destination connector without writing rows |
| `filedge requeue` | Move terminal failed files back to `PENDING` after remediation |

The typical workflow for a new pipeline:

```
filedge inspect data.csv --output pipeline.yaml          # 1. generate columns
# add format, dest_table, and connector fields
filedge validate data.csv --config pipeline.yaml         # 2. check it
filedge run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///filedge.db
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
- [Guides](guides/run.md) — one page per workflow
- [Scale ingestion](guides/scale.md) — large files, many files, parallel workers, and backfills
- [pipeline.yaml reference](reference/pipeline-yaml.md) — every config option
- [Connectors](reference/connectors.md) — destination setup for each backend
