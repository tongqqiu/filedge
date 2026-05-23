# pipeline.yaml reference

`pipeline.yaml` declares how a single ingestion pipeline behaves. One file per pipeline.

## Minimal example

```yaml
format: csv
dest_table: orders

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
```

## Full example

```yaml
format: csv
dest_table: orders
write_mode: append
retry_cap: 3
stale_timeout_minutes: 30
batch_size: 1000

connector:
  type: postgres
  url: postgresql://user:pass@host/dbname

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
  - source: notes
    dest: notes
    type: string
    required: false
```

---

## Top-level fields

### `format`

**Required.** The file format for files in the watched directory.

| Value | File types |
|-------|-----------|
| `csv` | `.csv` |
| `ndjson` | `.ndjson`, `.jsonl`, `.ndjson.gz` |

### `dest_table`

**Required.** The name of the destination table to write rows into.

### `write_mode`

**Optional.** Default: `append`.

| Value | Behaviour | Idempotency |
|-------|-----------|-------------|
| `append` | Rows added alongside prior records | Delete-where-hash then insert on retry |
| `truncate` | Table wiped then replaced with this file's rows | Inherently idempotent |

### `retry_cap`

**Optional.** Default: `3`. Maximum number of attempts before a file enters terminal `FAILED` state. Set to `1` to disable automatic retry.

### `stale_timeout_minutes`

**Optional.** Default: `30`. How long a `PROCESSING` lock may be held before it's reclaimed as stale.

### `batch_size`

**Optional.** Default: `1000`. Number of rows per database batch during `write_rows`. Larger batches are more efficient but use more memory.

---

## `connector` block

Declares the destination backend. See [Connectors](connectors.md) for full details on each type.

```yaml
connector:
  type: sqlite          # sqlite | postgres | bigquery | databricks | duckdb
  url: sqlite:///...    # type-specific options follow
```

---

## `columns` block

Declares the schema mapping between source file columns and destination table columns.

```yaml
columns:
  - source: <source_column_name>   # name as it appears in the file
    dest: <dest_column_name>       # name in the destination table
    type: <type>                   # see Column Types
    required: true | false
```

### `source`

The column name as it appears in the CSV header or NDJSON key.

### `dest`

The column name in the destination table. May differ from `source` for renaming.

### `type`

The target type for coercion. See [Column Types](column-types.md).

### `required`

Whether a missing or null value in this column should fail the row. When `required: true`, a null or missing value causes the file to fail (strict mode — the whole file is rejected, not just the row).

---

## Column tolerance

Extra columns in the source file that are not declared in `columns:` are silently ignored. Only declared columns are written to the destination. This lets upstream systems add fields without breaking your pipeline.

## Schema guard

On first run, the connector creates the destination table from the `columns:` block. On subsequent runs, if the live table schema doesn't match the config, the run fails loudly with a diff. No auto-migration — schema changes require manual action.
