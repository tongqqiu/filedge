# Getting Started

This guide walks you from a raw CSV file to a running pipeline in about 5 minutes.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) — the package manager used by this project

## Install

```bash
git clone https://github.com/tongqqiu/filedge.git
cd filedge
uv sync
```

The `filedge` command is now available in the project's virtual environment:

```bash
uv run filedge --help
```

To use it without `uv run`, activate the environment:

```bash
source .venv/bin/activate
filedge --help
```

---

## Step 1: Inspect your file

Start by pointing `filedge inspect` at your data file. It samples the first 1,000 rows and produces a ready-to-paste `columns:` block for `pipeline.yaml`.

```bash
filedge inspect data.csv
```

Output goes to stdout; a human-readable summary goes to stderr:

```
Columns: 4   High confidence: 3   Low confidence: 1   Ambiguous: 0

# Inferred from data.csv (1000 rows sampled)
columns:
  - source: order_id
    dest: order_id
    type: string
    required: true
  - source: amount
    dest: amount
    type: float
    required: true    # ⚠ low confidence — 3 null values in sample
  - source: order_date
    dest: order_date
    type: date
    required: false
  - source: customer_name
    dest: customer_name
    type: string
    required: true
```

Review columns marked **low confidence** or **ambiguous** before using them in production. See the [Inspect guide](guides/inspect.md) for details.

To write the output directly to a file:

```bash
filedge inspect data.csv --output pipeline.yaml
```

---

## Step 2: Complete the config

`filedge inspect` produces a `columns:` block. Wrap it in a full `pipeline.yaml`:

```yaml
format: csv
dest_table: orders
write_mode: append

connector:
  type: sqlite
  url: sqlite:///orders.db

columns:
  - source: order_id
    dest: order_id
    type: string
    required: true
  - source: amount
    dest: amount
    type: float
    required: true
  - source: order_date
    dest: order_date
    type: date
    required: false
  - source: customer_name
    dest: customer_name
    type: string
    required: true
```

See the [pipeline.yaml reference](reference/pipeline-yaml.md) for every available option.

---

## Step 3: Validate the config

Before writing any data, dry-run the file against your config:

```bash
filedge validate data.csv --config pipeline.yaml
```

Exit code `0` means clean; exit code `1` means failures were found:

```
✓ 1000 rows checked, no failures
```

Or with failures:

```
✗ row 42  amount  cannot coerce 'n/a' to float
✗ row 87  amount  cannot coerce '' to float (required)
2 failure(s) in 1000 rows checked
```

Fix the source data (or adjust `required: false` in the config) until validation is clean. See the [Validate guide](guides/validate.md) for more options.

---

## Step 4: Run the pipeline

Place your files in an incoming directory and run:

```bash
filedge run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///filedge.db
# Committed: 1  Failed: 0  Skipped: 0  New: 1  Reclaimed: 0  Retried: 0
```

`--audit-db-url` can also be set via the `FILEDGE_AUDIT_DB_URL` environment variable.

Check status any time:

```bash
filedge status --audit-db-url sqlite:///filedge.db
# PENDING:    0
# PROCESSING: 0
# COMMITTED:  1
# FAILED:     0
```

---

## Previewing rows

If validation reports a bad row, jump straight to it without opening the file in an editor:

```bash
filedge preview data.csv --start-row 42 --rows 5
```

See the [Preview guide](guides/preview.md) for details.

---

## Parquet files

Filedge supports Parquet natively. Install the optional extra first:

```bash
uv sync --extra parquet
```

Then use any read command as usual — the format is detected from the `.parquet` extension:

```bash
filedge inspect events.parquet
filedge preview events.parquet
filedge validate events.parquet --config pipeline.yaml
```

---

## Next steps

- [Preview guide](guides/preview.md) — spot-check files and jump to specific rows
- [Run guide](guides/run.md) — scheduling, retry behaviour, write modes
- [Connectors](reference/connectors.md) — switch from SQLite to PostgreSQL or BigQuery
- [Compact guide](guides/compact.md) — merge small files before ingestion
