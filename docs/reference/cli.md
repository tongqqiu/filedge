# CLI reference

All commands are accessed via the `etl` entry point.

```bash
etl --help
etl <command> --help
```

---

## `etl inspect`

Sample a file and generate a `columns:` block for `pipeline.yaml`.

```bash
etl inspect <file> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<file>` | required | File to inspect (local path or cloud URI) |
| `--format` | auto from extension | File format: `csv` or `ndjson` |
| `--sample-rows` | 1000 | Number of rows to sample |
| `--output` | stdout | Write YAML block to this file instead of stdout |

**Exit codes:** `0` on success, `1` on error.

See the [Inspect guide](../guides/inspect.md) for full details.

---

## `etl validate`

Dry-run a file against a `pipeline.yaml` config. No data is written.

```bash
etl validate <file> --config <path> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<file>` | required | File to validate (local path or cloud URI) |
| `--config` | required | Path to `pipeline.yaml` |
| `--format` | auto from extension | File format: `csv` or `ndjson` |
| `--sample-rows` | all rows | Validate only the first N rows |
| `--json` | off | Emit JSON to stdout in addition to text summary |

**Exit codes:** `0` clean, `1` failures found, `2` error (bad file path, bad config).

See the [Validate guide](../guides/validate.md) for full details.

---

## `etl compact`

Merge small NDJSON files into fewer, larger files before ingestion.

```bash
etl compact --watched-dir <path> --output <path> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--watched-dir` | required | Source prefix containing small files |
| `--output` | required | Output prefix for compacted files |
| `--max-files` | 1000 | Max input files per output batch |
| `--compress` | off | Gzip-compress output files (`.ndjson.gz`) |

**Exit codes:** `0` on success, `1` on error.

See the [Compact guide](../guides/compact.md) for full details.

---

## `etl run`

Ingest files from a watched directory with atomic commits, retry, and full audit trail.

```bash
etl run --dir <path> --config <path> --audit-db-url <url>
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--dir` | — | required | Watched directory path (local or cloud URI) |
| `--config` | — | required | Path to `pipeline.yaml` |
| `--audit-db-url` | `ETL_AUDIT_DB_URL` | required | Audit database URL |

**Exit codes:** `0` on success, `1` on error.

See the [Run guide](../guides/run.md) for full details.

---

## `etl status`

Show a summary of file states in the audit database.

```bash
etl status --audit-db-url <url> [--json]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--audit-db-url` | `ETL_AUDIT_DB_URL` | required | Audit database URL |
| `--json` | — | off | Output as JSON |

Example output:

```
PENDING:    0
PROCESSING: 0
COMMITTED:  47
FAILED:     1

Recent failures:
  bad_data.csv: cannot coerce 'n/a' to float
```

**Exit codes:** `0` on success, `1` on error.
