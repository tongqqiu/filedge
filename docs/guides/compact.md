# Compact small files

`filedge compact` merges many small NDJSON files from a source prefix into fewer, larger files in a separate output prefix. It's a pre-processing step — run it before `filedge run`.

## When to use it

The problem it solves: event streams and cloud object stores often produce thousands of tiny files (one per event, or one per API response page). Ingesting them one-by-one is expensive — each file is a separate database transaction, and cloud object stores charge per-listing operation.

Compaction groups them into batches, so `filedge run` processes far fewer, larger transactions.

Typical trigger: when your watched directory consistently contains more than a few hundred files per run.

## Basic usage

```bash
filedge compact --watched-dir ./incoming --output ./compacted
```

This reads all NDJSON files under `./incoming`, groups them into batches of up to 1,000 files each, and writes one merged NDJSON file per batch to `./compacted`. The originals in `./incoming` are **never modified**.

Then run the pipeline against the compacted output:

```bash
filedge run --dir ./compacted --config pipeline.yaml --audit-db-url sqlite:///filedge.db
```

## Cloud paths

Both `--watched-dir` and `--output` accept any [fsspec](https://filesystem-spec.readthedocs.io/en/latest/)-supported URI:

```bash
filedge compact \
  --watched-dir s3://my-bucket/landing/events/ \
  --output s3://my-bucket/compacted/
```

## Batch size

Control how many input files are merged into each output file:

```bash
filedge compact --watched-dir ./incoming --output ./compacted --max-files 500
```

Default is 1,000. Larger batches mean fewer output files but more memory per batch. For cloud warehouses like BigQuery or Databricks, larger batches often produce better bulk load performance.

## Compression

Write gzip-compressed output with `--compress`:

```bash
filedge compact --watched-dir ./incoming --output ./compacted --compress
```

Output files are named `<timestamp>_<batch>.ndjson.gz`. Use this when storage cost or transfer bandwidth matters. Your `filedge run` command handles `.ndjson.gz` files transparently — no config change needed.

## Output

```
Batches written: 3  Files compacted: 2847
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--watched-dir` | required | Source prefix containing small files |
| `--output` | required | Output prefix for compacted files |
| `--max-files` | 1000 | Max input files per output batch |
| `--compress` | off | Gzip-compress output files (`.ndjson.gz`) |
