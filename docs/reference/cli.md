# CLI reference

All commands are accessed via the `filedge` entry point.

```bash
filedge --help
filedge <command> --help
```

---

## `filedge inspect`

Sample a file and generate a `columns:` block for `pipeline.yaml`.

```bash
filedge inspect <file> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<file>` | required | File to inspect (local path or cloud URI) |
| `--format` | auto from extension | File format: `csv`, `ndjson`, or `parquet` |
| `--sample-rows` | 1000 | Number of rows to sample |
| `--output` | stdout | Write YAML block to this file instead of stdout |
| `--encoding` | `utf-8` | File encoding for text formats |

**Exit codes:** `0` on success, `1` on error.

See the [Inspect guide](../guides/inspect.md) for full details.

---

## `filedge preview`

Display rows of a file as a formatted table.

```bash
filedge preview <file> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<file>` | required | File to preview (local path or cloud URI) |
| `--format` | auto from extension | File format: `csv`, `ndjson`, or `parquet` |
| `--rows` | 10 | Number of rows to display |
| `--start-row` | 1 | First row to display (1-indexed) |
| `--encoding` | `utf-8` | File encoding for text formats |

**Exit codes:** `0` on success, `2` on error (bad file path, unrecognised format).

See the [Preview guide](../guides/preview.md) for full details.

---

## `filedge validate`

Dry-run a file against a `pipeline.yaml` config. No data is written.

```bash
filedge validate <file> --config <path> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<file>` | required | File to validate (local path or cloud URI) |
| `--config` | required | Path to `pipeline.yaml` |
| `--format` | auto from extension | File format: `csv`, `ndjson`, or `parquet` |
| `--sample-rows` | all rows | Validate only the first N rows |
| `--json` | off | Emit JSON to stdout in addition to text summary |
| `--encoding` | config value | Override file encoding from `pipeline.yaml` |

**Exit codes:** `0` clean, `1` failures found, `2` error (bad file path, bad config).

See the [Validate guide](../guides/validate.md) for full details.

---

## `filedge compact`

Merge small NDJSON files into fewer, larger files before ingestion.

```bash
filedge compact --watched-dir <path> --output <path> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--watched-dir` | required | Source prefix containing small files |
| `--output` | required | Output prefix for compacted files |
| `--max-files` | 1000 | Max input files per output batch |
| `--compress` | off | Gzip-compress output files (`.ndjson.gz`) |
| `--delete-source` | off | Delete source files after each batch commits |

**Exit codes:** `0` on success, `1` on error.

See the [Compact guide](../guides/compact.md) for full details.

---

## `filedge run`

Ingest files from a watched directory with retry-safe commits and a full audit trail.

```bash
filedge run --dir <path> --config <path> --audit-db-url <url> [--progress|--no-progress]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--dir` | — | required | Watched directory path (local or cloud URI) |
| `--config` | — | required | Path to `pipeline.yaml` |
| `--audit-db-url` | `FILEDGE_AUDIT_DB_URL` | required | Audit database URL |
| `--progress / --no-progress` | — | auto | Show live progress bars; defaults to on for interactive terminals |
| `--json` | — | off | Write the Run summary as JSON |
| `--log-format` | — | auto | `text` on a TTY, `json` otherwise |
| `--log-level` | — | `INFO` | Log level |
| `--otel-traces / --no-otel-traces` | `FILEDGE_OTEL_TRACES` | off | Enable OpenTelemetry tracing |
| `--otel-logs / --no-otel-logs` | `FILEDGE_OTEL_LOGS` | off | Enable the OpenTelemetry log bridge |

**Exit codes:** `0` on success, `1` on error.

See the [Run guide](../guides/run.md) for full details.

---

## `filedge healthcheck`

Probe the Audit DB and destination connector without writing data.

```bash
filedge healthcheck --config <path> --audit-db-url <url> [--json]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--config` | — | required | Path to `pipeline.yaml` |
| `--audit-db-url` | `FILEDGE_AUDIT_DB_URL` | required | Audit database URL |
| `--json` | — | off | Write one machine-readable health object |

**Exit codes:** `0` when all checks pass, `1` when any check fails.

See the [Healthcheck guide](../guides/healthcheck.md) for full details.

---

## `filedge requeue`

Move terminal `FAILED` files back to `PENDING` so the next run can retry them.

```bash
filedge requeue <filename> --audit-db-url <url>
filedge requeue --all-terminal-failed --yes --audit-db-url <url>
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `<filename>` | — | optional | Failed filename to requeue |
| `--hash` | — | optional | Content hash to disambiguate duplicate filenames |
| `--all-terminal-failed` | — | off | Select every terminal failed file |
| `--dry-run` | — | off | List bulk requeue candidates without changing state |
| `--yes` | — | off | Confirm bulk requeue |
| `--retry-cap` | — | `3` | Retry cap used to identify terminal failures |
| `--audit-db-url` | `FILEDGE_AUDIT_DB_URL` | required | Audit database URL |

**Exit codes:** `0` on success, `1` on error or missing bulk confirmation.

See the [Requeue failed files guide](../guides/requeue.md) for full details.

---

## `filedge status`

Show a summary of file states in the audit database.

```bash
filedge status --audit-db-url <url> [--json]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--audit-db-url` | `FILEDGE_AUDIT_DB_URL` | required | Audit database URL |
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

---

## `filedge lineage`

Show the full audit record and source-manifest metadata for one file.

```bash
filedge lineage <content-hash-or-filename> --audit-db-url <url> [--json]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `<content-hash-or-filename>` | — | required | Exact content hash or filename to inspect |
| `--audit-db-url` | `FILEDGE_AUDIT_DB_URL` | required | Audit database URL |
| `--json` | — | off | Emit machine-readable JSON |
| `--dest-table` | — | none | Destination table name to include in output |

When a filename maps to multiple content hashes, the command prints the matching hashes and exits non-zero. Re-run with one of those hashes to inspect a specific file.

**Exit codes:** `0` on success, `1` when no file matches, `2` when a filename is ambiguous.

See the [Source manifests guide](../guides/source-manifests.md) for full details.

---

## `filedge export-audit`

Generate a self-contained, read-only HTML audit site from the Audit DB.

```bash
filedge export-audit --audit-db-url <url> --output <path> [options]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--audit-db-url` | `FILEDGE_AUDIT_DB_URL` | required | Audit database URL |
| `--output` | — | required | Output path for the generated `index.html` |
| `--title` | — | none | Pipeline label shown in the site header |
| `--dest-table` | — | none | Destination table name for lineage SQL snippets |

**Exit codes:** `0` on success, `1` on error.

See the [Audit Export guide](../guides/audit-export.md) for full details.

---

## `filedge completion`

Print shell completion scripts for zsh or bash.

```bash
filedge completion [--shell zsh|bash]
```
