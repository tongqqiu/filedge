# Connectors

The destination backend is configured via a `connector:` block in `pipeline.yaml`. Credentials always come from environment variables — never from the YAML file.

---

## SQLite

Best for local development and lightweight deployments.

```yaml
connector:
  type: sqlite
  url: sqlite:///path/to/dest.db
```

No extra dependencies required.

---

## PostgreSQL

```yaml
connector:
  type: postgres
  url: postgresql://user:pass@host:5432/dbname
```

Or omit `url` and set the `DATABASE_URL` environment variable.

Install the driver:

```bash
uv sync --extra postgres
```

The connector writes rows via `executemany` with parameterized queries. Idempotency in append mode: rows for a given `file_hash` are deleted then re-inserted on retry, so a crashed run never produces duplicates.

---

## BigQuery

```yaml
connector:
  type: bigquery
  project: my-gcp-project
  dataset: my_dataset
```

Credentials from `GOOGLE_APPLICATION_CREDENTIALS` (Application Default Credentials).

Install the driver:

```bash
uv sync --extra bigquery
```

Idempotency in append mode is achieved by encoding the `file_hash` in the BigQuery load job ID. If a job with the same ID already succeeded, the retry is a no-op.

!!! warning "7-day job metadata limit"
    BigQuery only retains job metadata for 7 days. If a file is re-ingested more than 7 days after its original load, the retry will submit a new job and produce duplicate rows. For pipelines where re-ingestion after this window is possible, use `write_mode: truncate` or implement a pre-load DML `DELETE`.

---

## Databricks

```yaml
connector:
  type: databricks
  server_hostname: adb-xxx.azuredatabricks.net
  http_path: /sql/1.0/warehouses/xxx
  catalog: main
  schema: default
```

Auth token from `DATABRICKS_TOKEN`.

Install the driver:

```bash
uv sync --extra databricks
```

---

## DuckDB

Best for local analytics and lightweight OLAP deployments.

```yaml
connector:
  type: duckdb
  path: ./analytics.duckdb
```

Install the driver:

```bash
uv sync --extra duckdb
```

!!! note "Single writer"
    DuckDB supports only one writer at a time. The connector fails fast with a clear error if the file is locked by another process — it does not retry. Run `filedge run` serially, not concurrently, when using DuckDB.

---

## Adding a connector

Each connector implements a two-method interface:

- `ensure_table(config)` — create or validate the destination table
- `write_rows(table, rows, file_hash)` — write rows, idempotent per `file_hash`

See `etl/connectors/` for the existing implementations.
