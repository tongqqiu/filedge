# Inspect a file

`filedge inspect` samples a data file and produces a `columns:` block ready to paste into `pipeline.yaml`. It never writes data — it's a read-only operation.

## Basic usage

```bash
filedge inspect data.csv
filedge inspect events.ndjson
```

Format is auto-detected from the file extension (`.csv`, `.ndjson`, `.jsonl`, `.parquet`). Use `--format` to override:

```bash
filedge inspect data.txt --format csv
```

## Output

The YAML block goes to **stdout**; a human-readable summary goes to **stderr**. This keeps them composable with shell redirection:

```bash
# write config to file, summary still prints to terminal
filedge inspect data.csv > pipeline.yaml

# or use --output
filedge inspect data.csv --output pipeline.yaml
```

Example summary (stderr):

```
Columns: 4   High confidence: 3   Low confidence: 1   Ambiguous: 0
```

Example columns block (stdout):

```yaml
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
  - source: status
    dest: status
    type: string
    required: true
  - source: created_at
    dest: created_at
    type: timestamp
    required: false
```

## Confidence tiers

Each column is annotated with a confidence tier:

| Tier | Meaning | What to do |
|------|---------|-----------|
| **high** | All sampled values parse cleanly, no nulls | Use as-is |
| **low** | Most values parse but exceptions found | Review null count and unparseable values shown in comment |
| **ambiguous** | Conflicting evidence — e.g. two date formats, or values that look like both boolean and integer | Inspect the raw data and choose the right type manually |

Review every **low** and **ambiguous** column before committing the config to production.

## Sampling

By default, the first 1,000 rows are sampled. Use `--sample-rows` to change this:

```bash
filedge inspect data.csv --sample-rows 5000
```

A larger sample catches rare types and edge-case nulls. For very large files, sampling more rows adds latency — 1,000 is usually enough for well-structured data.

## Cloud paths

`filedge inspect` accepts any path supported by [fsspec](https://filesystem-spec.readthedocs.io/en/latest/):

```bash
filedge inspect s3://my-bucket/landing/data.csv
filedge inspect gs://my-bucket/events.ndjson
```

Cloud dependencies must be installed separately:

```bash
uv sync --extra s3   # for S3
uv sync --extra gcs  # for GCS
```

## Parquet files

Parquet files store schema metadata, so `filedge inspect` reads the embedded schema directly — no row sampling needed. All columns are reported with **high** confidence.

```bash
filedge inspect events.parquet
```

!!! note "Parquet requires pyarrow"
    ```bash
    uv sync --extra parquet
    ```

Nested Parquet columns (structs and lists) are mapped to `string` with a note listing the nested keys:

```yaml
  - source: address
    dest: address
    type: string
    required: false   # ⚠ nested struct — keys: city, zip
```

## NDJSON nested objects

When a field in a NDJSON file contains a nested object (e.g. `{"address": {"city": "NYC"}}`), `filedge inspect` surfaces it as a `string` column with a warning listing the nested keys:

```yaml
  - source: address
    dest: address
    type: string
    required: false   # ⚠ nested object — keys: city, zip, country
```

The pipeline has no flattening step, so a `string` column is the safe choice. If you need individual nested fields, flatten the data upstream before ingestion.

## Plain JSON files (`.json`)

Filedge supports **newline-delimited JSON** (NDJSON / JSONL), not single-document JSON. A `.json` file containing one array or one object is not recognized — `filedge inspect` returns a "format not detected" error. This is intentional: the ingestion path is streaming, and a single top-level JSON value cannot be consumed row-by-row.

Convert to NDJSON upstream with `jq` (one shell line):

```bash
# Array of objects → one object per line
jq -c '.[]' events.json > events.ndjson

# Envelope shape {"data": [...]} → one object per line
jq -c '.data[]' events.json > events.ndjson

# Single object → one-row NDJSON file
jq -c '.' record.json > record.ndjson
```

Then inspect the NDJSON:

```bash
filedge inspect events.ndjson
```

For data sources you control, the cleaner fix is to have your [Fetcher](../../CONTEXT.md#fetcher) or [Queue Materializer](../../CONTEXT.md#queue-materializer) write NDJSON directly to the Watched Directory.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--format` | auto from extension | File format: `csv`, `ndjson`, or `parquet` |
| `--sample-rows` | 1000 | Number of rows to sample |
| `--output` | stdout | Write the YAML block to this file instead of stdout |
