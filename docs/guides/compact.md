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

This reads all NDJSON files under `./incoming`, groups them into batches of up to 1,000 files each, and writes one merged NDJSON file per batch to `./compacted`. The originals in `./incoming` are **not deleted** by default.

Then run the pipeline against the compacted output:

```bash
filedge run --dir ./compacted --config pipeline.yaml --audit-db-url sqlite:///filedge.db
```

## Resilience and idempotency

Compact and run are designed to run **sequentially, never concurrently**. Two modes determine how compact handles re-runs.

### Manifest mode (default — read-only source)

When you do not have permission to delete source files, compact maintains a manifest at `<output>/.filedge/compact_manifest.ndjson`. Each line records the source filenames included in one batch. On every subsequent run, already-recorded files are skipped automatically — re-running compact is safe with no duplicate output.

```bash
filedge compact --watched-dir s3://bucket/landing/ --output s3://bucket/compacted/
# Re-run later: only new files in landing/ are processed
filedge compact --watched-dir s3://bucket/landing/ --output s3://bucket/compacted/
```

The manifest lives in a hidden subdirectory (`.filedge/`) so `filedge run` never picks it up as a data file.

### Delete-source mode (has delete permission)

When you can delete source files, use `--delete-source`. After each batch is committed, the source files that went into it are deleted. The absence of source files is the idempotency signal — re-running compact is a no-op when the source directory is empty.

```bash
filedge compact --watched-dir ./incoming --output ./compacted --delete-source
```

### Crash recovery

Each batch is written atomically: data flows to a `.tmp` file, which is renamed to the final name only after the write completes. A crash during a write leaves a `.tmp` file that is cleaned up on the next run; the batch is retried from scratch.

If a crash occurs **after** the rename but **before** the source delete (or manifest append), the next run reprocesses those source files and produces a duplicate batch file in the output directory. This is safe: `filedge run` deduplicates by content hash, so duplicate batches are silently skipped at load time.

!!! tip "`.tmp` guard"
    `filedge run` always filters `.tmp` files from the watched directory, so a half-written compact output can never be ingested even if scheduling slips.

## Cloud paths

Both `--watched-dir` and `--output` accept any [fsspec](https://filesystem-spec.readthedocs.io/en/latest/)-supported URI:

```bash
filedge compact \
  --watched-dir s3://my-bucket/landing/events/ \
  --output s3://my-bucket/compacted/
```

On S3 and GCS, rename is implemented as copy + delete (not a true atomic operation). The crash window between copy and delete is small; any orphaned `.tmp` files are ignored by `filedge run`.

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
| `--delete-source` | off | Delete source files after each batch commits |
