# Preview a file

`filedge preview` displays a formatted table of rows from any supported file — useful for spot-checking data or inspecting specific rows flagged as problematic.

```bash
filedge preview <file> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<file>` | required | File to preview (local path or cloud URI) |
| `--format` | auto from extension | File format: `csv`, `ndjson`, or `parquet` |
| `--rows` | 10 | Number of rows to display |
| `--start-row` | 1 | First row to display (1-indexed) |

**Exit codes:** `0` on success, `2` on error.

---

## Basic usage

```bash
filedge preview data.csv
```

Output is a fixed-width ASCII table with row numbers:

```
  # │ name    │ amount │ order_date
────┼─────────┼────────┼────────────
  1 │ Alice   │ 9.99   │ 2024-01-15
  2 │ Bob     │ 14.50  │ 2024-01-16
  3 │ Carol   │ 0.01   │ 2024-01-16
```

---

## Jumping to a specific row

If a log or validation report says row 5,000 has bad data, jump directly to it:

```bash
filedge preview data.csv --start-row 5000 --rows 5
```

```
     # │ name  │ amount │ order_date
───────┼───────┼────────┼────────────
  5000 │ Dave  │ n/a    │ 2024-03-01
  5001 │ Eve   │ 7.25   │ 2024-03-01
  5002 │ Frank │ 12.00  │ 2024-03-02
  5003 │ Grace │ -      │ 2024-03-03
  5004 │ Heidi │ 3.50   │ 2024-03-04
```

This is especially useful for large files that are impractical to open in a text editor.

---

## Wide files

When a file has more columns than fit in 120 characters, `preview` shows the columns that fit and lists the rest below the table:

```
  # │ id │ name    │ amount
────┼────┼─────────┼────────
  1 │ 1  │ Alice   │ 9.99
  2 │ 2  │ Bob     │ 14.50

Columns not shown (too wide): description, tags, metadata, created_at
```

---

## Parquet files

Parquet is detected automatically from the `.parquet` extension:

```bash
filedge preview events.parquet --rows 5
```

Or use `--format parquet` explicitly:

```bash
filedge preview events --format parquet
```

!!! note "Parquet requires pyarrow"
    Install the optional `parquet` extra first:
    ```bash
    uv sync --extra parquet
    ```

---

## Cloud files

Preview files on S3, GCS, or Azure Blob Storage using their native URIs:

```bash
filedge preview s3://my-bucket/uploads/data.csv
filedge preview gs://my-bucket/data.ndjson
```

Credentials are picked up from the environment (AWS profile, `GOOGLE_APPLICATION_CREDENTIALS`, etc.).
