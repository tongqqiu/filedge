# Run a pipeline

`filedge run` is the main ingestion command. It scans a watched directory, enqueues new files, and processes them with retry-safe destination commits, automatic retry, and a full audit trail.

## Basic usage

```bash
filedge run \
  --dir ./incoming \
  --config pipeline.yaml \
  --audit-db-url sqlite:///filedge.db
```

Or set the audit DB via environment variable:

```bash
export FILEDGE_AUDIT_DB_URL=sqlite:///filedge.db
filedge run --dir ./incoming --config pipeline.yaml
```

## What happens on each run

```
Run N
├── Reset FAILED files below retry_cap → PENDING
├── Reclaim stale PROCESSING locks → PENDING
├── Connector: ensure destination table exists (create or validate schema)
├── Hash all files in watched directory
├── Enqueue new content hashes as PENDING
└── For each PENDING file:
    ├── Audit DB: mark PROCESSING (distributed lock)
    ├── Connector: write_rows — stream rows through parser + transform
    │   └── Connector commits its own transaction (idempotent per file_hash)
    └── Audit DB: mark COMMITTED  (or mark FAILED on error)
```

Files already `COMMITTED` are silently skipped — their content hash is already in the audit DB.

## Output

In an interactive terminal, `filedge run` shows live progress for hashing,
registering, and loading files. The loading progress counts only files eligible
to process in this run, and the current file shows a throttled row count while
rows are streamed. Use `--no-progress` to keep output compact, or `--progress`
to force progress rendering.

```
Committed: 3  Failed: 0  Skipped: 0  New: 3  Reclaimed: 0  Retried: 0
```

| Field | Meaning |
|-------|---------|
| Committed | Files successfully written this run |
| Failed | Files that failed this run |
| Skipped | Terminal failures (hit retry cap) |
| New | Files discovered for the first time |
| Reclaimed | Stale PROCESSING locks recovered |
| Retried | Previously-failed files retried this run |

### Machine-readable summary (`--json`)

For scheduler integration, pass `--json` to receive the Run summary as a single
JSON line on stdout (suppressing the human-readable line):

```bash
filedge run --dir ./incoming --config pipeline.yaml --json
```

```json
{"run_id": "f3c8…", "started_at": "2026-05-24T14:00:00+00:00", "finished_at": "…", "duration_s": 1.42, "files_scanned": 12, "new_files": 3, "committed": 3, "failed": 0, "skipped": 0, "reclaimed": 0, "retried": 0, "rows_committed": 4218, "bytes_processed": 184320}
```

`run_id` is a UUID4 unique to each Run. It is stamped on every audit row
processed by the Run (column `run_id` in `etl_file_audit`) and on every log line
emitted during the Run — so you can correlate stdout, logs, and the audit DB.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Run completed; zero files failed |
| `1` | Run completed with at least one failed file, or aborted with an error |

Schedulers (cron, Airflow, K8s CronJob) should key off this exit code rather
than parsing stdout.

### Structured logs

Pipeline progress is also emitted to stderr as log lines. Defaults are
TTY-aware: human-readable text in an interactive terminal, JSON when stderr is
redirected to a file or scheduler. Override explicitly:

```bash
filedge run … --log-format json --log-level INFO
```

| Option | Default | Values |
|--------|---------|--------|
| `--log-format` | `text` if TTY else `json` | `text`, `json` |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Each JSON line carries `ts`, `level`, `logger`, `event`, `run_id`, and
event-specific fields like `phase`, `action`, `path`, `rows`, `error` — ready
to ingest into Loki, Datadog, or any log pipeline.

## Checking status

```bash
filedge status --audit-db-url sqlite:///filedge.db
```

```
PENDING:    0
PROCESSING: 0
COMMITTED:  47
FAILED:     1

Recent failures:
  bad_data.csv: cannot coerce 'n/a' to float (row 12, column: amount)
```

Add `--json` for machine-readable output.

## Retry behaviour

A file that fails is marked `FAILED` with `attempt_count = 1`. On the next run, if `attempt_count < retry_cap`, it is reset to `PENDING` and retried. Once `attempt_count >= retry_cap`, the file is terminal — it stays `FAILED` and counts as `skipped` in the run summary.

The default `retry_cap` is 3. Configure it in `pipeline.yaml`:

```yaml
retry_cap: 5
```

To manually re-queue a terminal failure (after fixing the source data), use `filedge requeue`:

```bash
filedge requeue bad_data.csv
```

See the [Requeue failed files](requeue.md) guide for the full workflow, bulk recovery, and disambiguation options.

## Stale lock recovery

If a worker crashes while processing a file, it leaves that file in `PROCESSING`. On the next run, locks older than `stale_timeout_minutes` (default: 30) are automatically reclaimed and re-queued as `PENDING`. The connector's per-file-hash idempotency ensures no duplicate rows when the file is retried.

Configure the timeout:

```yaml
stale_timeout_minutes: 60
```

## Schema guard

On first run against a new destination table, the connector creates the table from the `pipeline.yaml` schema. On subsequent runs, if the config and the live table don't match, the run fails with a clear diff — no silent auto-migration.

To change the schema, you must alter the destination table manually and then update `pipeline.yaml` to match.

## Scheduling

`filedge run` is designed to be invoked by an external scheduler and then exit. It does not run as a daemon.

=== "cron"

    ```cron
    # Run every 15 minutes
    */15 * * * * cd /app && FILEDGE_AUDIT_DB_URL=sqlite:///filedge.db filedge run --dir ./incoming --config pipeline.yaml
    ```

=== "Kubernetes CronJob"

    ```yaml
    apiVersion: batch/v1
    kind: CronJob
    metadata:
      name: filedge-pipeline
    spec:
      schedule: "*/15 * * * *"
      jobTemplate:
        spec:
          template:
            spec:
              containers:
              - name: filedge
                image: your-filedge-image
                command: ["filedge", "run", "--dir", "/data/incoming", "--config", "/config/pipeline.yaml"]
                env:
                - name: FILEDGE_AUDIT_DB_URL
                  valueFrom:
                    secretKeyRef:
                      name: filedge-secrets
                      key: audit-db-url
              restartPolicy: OnFailure
    ```

## Filtering files

By default, `filedge run` processes every file directly under `--dir`. Use `file_pattern` in `pipeline.yaml` to restrict which files are picked up:

```yaml
file_pattern: "*.csv"
```

Any glob syntax supported by your filesystem works: `orders_*.csv`, `*.ndjson`, etc. On S3 and GCS, patterns with a fixed prefix (e.g. `report_*.csv`) use the storage API's native prefix filter — more efficient than a bare `*.csv` which lists all objects first and then filters client-side.

Subdirectories are never scanned regardless of the pattern. Filedge is a flat drop-zone: place files directly in the watched directory.

## Scale limits

Filedge is designed for watched directories of up to **~50,000 files**. Within that range:

- All file paths and their SHA-256 hashes are held in memory during a run (~2 MB at 50K files).
- Registration uses a batched `SELECT … IN (…)` query — a single round-trip regardless of file count.
- Each file is processed and committed individually, so memory per file is bounded by `batch_size` rows (default 1,000).

For SQLite as the **destination** (not the audit DB), the connector holds an exclusive write lock for the duration of each file's insert. Run filedge as a single process against a SQLite destination; concurrent writers will contend on the lock.

For parallel workers, large files, and backfills, see [Scale ingestion](scale.md).

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dir` | required | Watched directory path (local or cloud URI) |
| `--config` | required | Path to `pipeline.yaml` |
| `--audit-db-url` | `$FILEDGE_AUDIT_DB_URL` | Audit database URL |
| `--progress` / `--no-progress` | auto (TTY-detect) | Toggle the Rich progress UI |
| `--json` | off | Emit the Run summary as a JSON line on stdout |
| `--log-format` | auto (TTY-detect) | `text` or `json` for stderr logs |
| `--log-level` | `INFO` | Threshold for stderr logs |
