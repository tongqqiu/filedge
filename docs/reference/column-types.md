# Column types

The `type:` field in a `columns:` block declares how source values are coerced and how the destination column is typed.

## Supported types

| Type | Python coercion | PostgreSQL | BigQuery | SQLite | DuckDB |
|------|----------------|------------|---------|--------|--------|
| `string` | `str(value)` | `TEXT` | `STRING` | `TEXT` | `VARCHAR` |
| `integer` | `int(value)` | `INTEGER` | `INT64` | `INTEGER` | `INTEGER` |
| `float` | `float(value)` | `DOUBLE PRECISION` | `FLOAT64` | `REAL` | `DOUBLE` |
| `date` | ISO 8601 string | `DATE` | `DATE` | `TEXT` | `DATE` |
| `timestamp` | ISO 8601 string | `TIMESTAMP WITH TIME ZONE` | `TIMESTAMP` | `TEXT` | `TIMESTAMP` |
| `boolean` | truthy string coercion | `BOOLEAN` | `BOOL` | `INTEGER` | `BOOLEAN` |

## Coercion rules

**`string`** — Any value is accepted. `None` becomes an empty string unless `required: true`.

**`integer`** — Parsed via `int()`. Fails on non-numeric strings. Floats like `"3.0"` are rejected unless they parse to a whole number.

**`float`** — Parsed via `float()`. Accepts integers, decimals, and scientific notation. Fails on non-numeric strings.

**`date`** — Expects ISO 8601 format: `YYYY-MM-DD`. Stored as a date in databases that have a native date type (PostgreSQL, BigQuery, DuckDB) or as a `TEXT` string in SQLite.

**`timestamp`** — Expects ISO 8601 format: `YYYY-MM-DDTHH:MM:SS[Z|±HH:MM]`. Stored with timezone where supported.

**`boolean`** — Truthy strings: `"true"`, `"1"`, `"yes"` (case-insensitive) → `True`. Falsy strings: `"false"`, `"0"`, `"no"` → `False`. Anything else fails.

## Coercion failures

A coercion failure on any row causes the **entire file** to fail — no rows are committed. This is [strict mode](../architecture/decisions.md#adr-0003) — partial commits are not supported.

## Row provenance columns

Every destination row automatically receives two extra columns:

| Column | Type | Description |
|--------|------|-------------|
| `_source_file_hash` | string | SHA-256 of the source file |
| `_ingested_at` | timestamp | UTC timestamp of when the row was written |

These are added by the connector and do not need to be declared in `pipeline.yaml`.
