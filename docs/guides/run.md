# Run a pipeline

`etl run` is the main ingestion command. It scans a watched directory, enqueues new files, and processes them with atomic commits, automatic retry, and a full audit trail.

## Basic usage

```bash
etl run \
  --dir ./incoming \
  --config pipeline.yaml \
  --audit-db-url sqlite:///etl.db
```

Or set the audit DB via environment variable:

```bash
export ETL_AUDIT_DB_URL=sqlite:///etl.db
etl run --dir ./incoming --config pipeline.yaml
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

## Checking status

```bash
etl status --audit-db-url sqlite:///etl.db
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

To manually re-queue a terminal failure (after fixing the source data):

```sql
UPDATE etl_file_audit SET state='PENDING', attempt_count=0 WHERE content_hash='<hash>';
```

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

`etl run` is designed to be invoked by an external scheduler and then exit. It does not run as a daemon.

=== "cron"

    ```cron
    # Run every 15 minutes
    */15 * * * * cd /app && ETL_AUDIT_DB_URL=sqlite:///etl.db etl run --dir ./incoming --config pipeline.yaml
    ```

=== "Kubernetes CronJob"

    ```yaml
    apiVersion: batch/v1
    kind: CronJob
    metadata:
      name: etl-pipeline
    spec:
      schedule: "*/15 * * * *"
      jobTemplate:
        spec:
          template:
            spec:
              containers:
              - name: etl
                image: your-etl-image
                command: ["etl", "run", "--dir", "/data/incoming", "--config", "/config/pipeline.yaml"]
                env:
                - name: ETL_AUDIT_DB_URL
                  valueFrom:
                    secretKeyRef:
                      name: etl-secrets
                      key: audit-db-url
              restartPolicy: OnFailure
    ```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dir` | required | Watched directory path (local or cloud URI) |
| `--config` | required | Path to `pipeline.yaml` |
| `--audit-db-url` | `$ETL_AUDIT_DB_URL` | Audit database URL |
