# Filedge DuckDB Live Demo

This demo is the zero-credential live path for Filedge. It runs entirely from a
fresh clone, GitHub Codespaces, or a local shell:

- CSV files land in a watched directory.
- Filedge validates and loads them into DuckDB.
- SQLite records the audit state.
- A rerun proves content-hash idempotency.
- A bad file proves failure visibility.
- Audit Export produces a static HTML evidence page.

Run commands from the repository root.

## Setup

```bash
uv sync --extra dev --extra duckdb
```

## Fast path

Run the whole story:

```bash
./demo/duckdb/scripts/run_demo.sh
```

The script writes generated artifacts to `demo/duckdb/out/`, which is ignored by
git.

## Step-by-step presenter flow

Reset generated files:

```bash
./demo/duckdb/scripts/reset.sh
```

Inspect a file and validate it against the committed contract:

```bash
uv run filedge inspect demo/duckdb/incoming/orders_001.csv
uv run filedge validate demo/duckdb/incoming/orders_001.csv \
  --config demo/duckdb/pipeline.yaml
```

Load the good files:

```bash
uv run filedge run \
  --dir demo/duckdb/incoming \
  --config demo/duckdb/pipeline.yaml \
  --audit-db-url sqlite:///demo/duckdb/out/audit.db
```

Show audit state and destination rows:

```bash
uv run filedge status --audit-db-url sqlite:///demo/duckdb/out/audit.db
uv run python demo/duckdb/scripts/show_rows.py
```

Run the same load again to prove reruns do not duplicate rows:

```bash
uv run filedge run \
  --dir demo/duckdb/incoming \
  --config demo/duckdb/pipeline.yaml \
  --audit-db-url sqlite:///demo/duckdb/out/audit.db

uv run python demo/duckdb/scripts/show_rows.py
```

Introduce a bad file and show the failure trail:

```bash
cp demo/duckdb/bad/orders_bad.csv demo/duckdb/incoming/orders_bad.csv

uv run filedge run \
  --dir demo/duckdb/incoming \
  --config demo/duckdb/pipeline.yaml \
  --audit-db-url sqlite:///demo/duckdb/out/audit.db

uv run filedge status --audit-db-url sqlite:///demo/duckdb/out/audit.db
```

Export the audit page:

```bash
uv run filedge export-audit \
  --audit-db-url sqlite:///demo/duckdb/out/audit.db \
  --output demo/duckdb/out/audit-site/index.html \
  --title "Filedge DuckDB Demo" \
  --dest-table orders
```

Open `demo/duckdb/out/audit-site/index.html` in a browser.

## Cloud variants

Keep this DuckDB demo as the live baseline because it has no credentials or
billing risk. For cloud demos, keep the file inputs and pipeline columns the
same, then swap only the `connector:` block:

- Supabase or Neon Postgres: `type: postgres`, with `DATABASE_URL` set in the
  environment.
- BigQuery: `type: bigquery`, with a demo dataset and application credentials.
- Snowflake: `type: snowflake`, using a trial account and key-pair auth.
- Databricks: `type: databricks`, with SQL warehouse, token, and staging
  location configured.

That is the demo point: the operational contract stays stable while the
destination changes.
