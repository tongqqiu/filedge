# Filedge

**Reliable file ingestion for data engineers.** Filedge loads files into your
warehouse with per-file atomic commits, content-hash idempotency, crash-safe
retry, and a full audit trail — so a failed load never leaves you guessing what
made it in.

[Get started in 5 minutes](getting-started.md){ .md-button .md-button--primary }
[See it end to end](tutorials/index.md){ .md-button }

---

## The problem

Most ETL pipelines fail in the worst possible way: they write half the rows,
then crash, leaving the destination corrupt with no record of what happened.
Re-running risks double-writing the rows that already succeeded.

## How Filedge is different

Three guarantees, enforced on every file:

- **Atomic commits — no partial loads.** Each destination connector makes a
  file's write retry-safe for its content hash. A crash mid-load never leaves
  half the rows behind.
- **Content-hash idempotency — no accidental re-ingestion.** Files are
  identified by SHA-256 of their content, not filename. Renaming a file doesn't
  reload it; replacing it with new content does.
- **A full audit trail — always know what loaded.** Every file moves through a
  `PENDING → PROCESSING → COMMITTED/FAILED` state machine, stored alongside
  row-level provenance you can query and export.

The boundary is always the **File**: anything upstream — file drops, API pulls,
queues, database exports — becomes a complete File in a watched directory, and
`filedge run` ingests it the same audited way.

## 30-second example

```bash
filedge inspect data.csv --output pipeline.yaml      # 1. infer the schema
# add format, dest_table, and a connector: block
filedge validate data.csv --config pipeline.yaml     # 2. dry-run, no data written
filedge run --dir ./incoming --config pipeline.yaml \
  --audit-db-url sqlite:///filedge.db                # 3. ingest with audit trail
# Committed: 1  Failed: 0  Skipped: 0  New: 1
```

Prefer an interactive flow? `filedge author data.csv` walks you through
inference, schema review, write mode, connector, and validation, then writes a
ready-to-run pipeline folder. See the [Author guide](guides/author.md).

## Destinations

Pluggable via a `connector:` block in `pipeline.yaml`:

- **SQLite** — local dev and lightweight deployments
- **PostgreSQL** — production workloads
- **BigQuery** — GCP data warehouses
- **Databricks** — Databricks SQL warehouses
- **DuckDB** — local analytics

---

## Where to go next

| If you want to… | Go to |
|-----------------|-------|
| Install and run your first pipeline | [Getting Started](getting-started.md) |
| See the full value end to end | [Tutorials](tutorials/index.md) |
| Get a specific job done | [How-to guides](guides/index.md) |
| Look up a config option or command | [Reference](reference/pipeline-yaml.md) |
| Understand how it works under the hood | [Architecture](architecture/index.md) |
