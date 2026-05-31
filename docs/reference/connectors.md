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

Supports `write_mode: cdc` for SCD Type 1 CDC Files.

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

Supports `write_mode: cdc` for SCD Type 1 CDC Files.

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

Idempotency in append mode is achieved by encoding the destination table and `file_hash` in the BigQuery load job ID. If a job with the same ID already succeeded, the retry is a no-op.

!!! warning "7-day job metadata limit"
    BigQuery only retains job metadata for 7 days. If a file is re-ingested more than 7 days after its original load, the retry will submit a new job and produce duplicate rows. For pipelines where re-ingestion after this window is possible, use `write_mode: truncate` or implement a pre-load DML `DELETE`.

### BigQuery integration tests

Live BigQuery integration tests are opt-in and skipped by default. They require a pre-created test dataset:

```bash
export FILEDGE_BIGQUERY_INTEGRATION=1
export BIGQUERY_PROJECT=my-gcp-project
export BIGQUERY_DATASET=filedge_ci_test
uv sync --extra dev --extra bigquery
uv run pytest tests/test_connector_bigquery.py
```

For GitHub Actions, prefer Workload Identity Federation with a dedicated CI service account instead of a service account key. The service account should have `roles/bigquery.jobUser` on the project and `roles/bigquery.dataEditor` on only the test dataset. The included `BigQuery Integration` workflow expects:

- GitHub secrets: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_BIGQUERY_TEST_SERVICE_ACCOUNT`
- GitHub variables: `BIGQUERY_PROJECT`, `BIGQUERY_DATASET`

---

## Databricks

```yaml
connector:
  type: databricks
  server_hostname: adb-xxx.azuredatabricks.net
  http_path: /sql/1.0/warehouses/xxx
  catalog: main
  schema: default
  staging_location: s3://my-bucket/filedge-staging
```

Auth token from `DATABRICKS_TOKEN`.

`staging_location` may also be supplied via `DATABRICKS_STAGING_LOCATION`. It must be a cloud or mounted location the Databricks SQL warehouse can read with `COPY INTO`, such as S3, ADLS Gen2, GCS, or a Unity Catalog volume path like `/Volumes/workspace/default/test/filedge-staging`.

When `staging_location` starts with `/Volumes/`, Filedge uploads the temporary NDJSON file with the Databricks Files API before running `COPY INTO`, then removes it after the load. The token must have permission to write files in the target volume.

Install the driver:

```bash
uv sync --extra databricks
```

Append mode stages each file as newline-delimited JSON and runs `COPY INTO` into a temporary staging table, then `MERGE INTO` the destination on `_source_file_hash`. Re-running the same file is a no-op for rows that already committed. Truncate mode truncates the destination and inserts the staged rows.

### Databricks integration tests

Live Databricks integration tests are opt-in and skipped by default. They require a SQL warehouse plus a `staging_location` that the warehouse can read with `COPY INTO`:

```bash
export FILEDGE_DATABRICKS_INTEGRATION=1
export DATABRICKS_TOKEN=...
export DATABRICKS_SERVER_HOSTNAME=dbc-xxx.cloud.databricks.com
export DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxx
export DATABRICKS_CATALOG=workspace
export DATABRICKS_SCHEMA=default
export DATABRICKS_STAGING_LOCATION=/Volumes/workspace/default/test/filedge-staging
uv sync --extra dev --extra databricks
uv run pytest tests/test_connector_databricks_integration.py
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

## Snowflake

```yaml
connector:
  type: snowflake
  account: myorg-myaccount
  user: FILEDGE_LOADER
  warehouse: LOAD_WH
  database: RAW
  schema: PUBLIC
  # role: FILEDGE_ROLE   # optional
```

The password is supplied at runtime via the `SNOWFLAKE_PASSWORD` environment
variable (or secrets mount) — never written to `pipeline.yaml`.

Install the driver:

```bash
uv sync --extra snowflake
```

Idempotency in append mode is achieved the same way as PostgreSQL: a
`DELETE WHERE _source_file_hash = <hash>` followed by a batched `INSERT`, run in
one transaction. Re-loading the same File is a no-op; a failed load rolls back
and leaves the table untouched. CDC files apply row-by-row in a transaction.

!!! note "Quoted identifiers"
    Every identifier is double-quoted, so column and table names are stored in
    Snowflake exactly as written in `pipeline.yaml` (e.g. `order_id`,
    `_source_file_hash`) rather than folded to upper case. Reference an
    `_source_file_hash` lineage query with the same lower-case, quoted name.

### Snowflake integration tests

Live Snowflake integration tests are opt-in and skipped by default. They require
an account, warehouse, database, and schema the user can create tables in:

```bash
export FILEDGE_SNOWFLAKE_INTEGRATION=1
export SNOWFLAKE_ACCOUNT=myorg-myaccount
export SNOWFLAKE_USER=FILEDGE_LOADER
export SNOWFLAKE_WAREHOUSE=LOAD_WH
export SNOWFLAKE_DATABASE=RAW
export SNOWFLAKE_SCHEMA=PUBLIC
export SNOWFLAKE_PASSWORD=...
uv sync --extra dev --extra snowflake
uv run pytest tests/test_connector_snowflake_integration.py
```

The included `Snowflake Integration` workflow reads `SNOWFLAKE_ACCOUNT`,
`SNOWFLAKE_USER`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`
(and optional `SNOWFLAKE_ROLE`) from repository variables and `SNOWFLAKE_PASSWORD`
from secrets.

---

## Adding a connector

Each connector implements a two-method interface:

- `ensure_table(config)` — create or validate the destination table
- `write_rows(table, rows, file_hash)` — write rows, idempotent per `file_hash`

See `filedge/connectors/` for the existing implementations.
