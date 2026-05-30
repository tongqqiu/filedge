# Scale ingestion

Use Filedge at scale by controlling three things:

- how many files each Run scans
- how much work each file contains
- how many workers write to the destination at the same time

The safest pattern is to split incoming data into bounded partitions, run one or more workers per partition, and let the Audit DB coordinate which worker owns each File.

## Quick recipe

1. Land complete files into time-partitioned prefixes:

   ```
   s3://warehouse-landing/orders/dt=2026-05-24/hour=00/
   s3://warehouse-landing/orders/dt=2026-05-24/hour=01/
   ```

2. Compact tiny files before loading:

   ```bash
   filedge compact \
     --watched-dir s3://warehouse-landing/orders/dt=2026-05-24/hour=00/ \
     --output s3://warehouse-landing/orders-compacted/dt=2026-05-24/hour=00/ \
     --max-files 5000 \
     --compress
   ```

3. Run workers against bounded prefixes:

   ```bash
   filedge run \
     --dir s3://warehouse-landing/orders-compacted/dt=2026-05-24/hour=00/ \
     --config pipeline.yaml \
     --audit-db-url "$FILEDGE_AUDIT_DB_URL" \
     --json \
     --log-format json \
     --no-progress
   ```

4. Use a shared PostgreSQL Audit DB for parallel workers:

   ```bash
   export FILEDGE_AUDIT_DB_URL=postgresql://filedge:secret@audit-db:5432/filedge
   ```

5. Set `stale_timeout_minutes` longer than the slowest normal file load:

   ```yaml
   stale_timeout_minutes: 180
   batch_size: 5000
   ```

## Scale dimensions

| Need | Use |
|------|-----|
| Too many tiny files | `filedge compact` before `filedge run` |
| Too many files to scan | Partition the Watched Directory by time, tenant, region, or source |
| Very large files | Increase `batch_size` within memory limits, or split upstream into multiple complete files |
| More throughput | Run parallel workers on separate prefixes or non-overlapping file patterns |
| Shared worker coordination | Use a PostgreSQL Audit DB |
| Cheap local development | SQLite destination and audit DB, single worker |

## Partition the Watched Directory

`filedge run` scans one flat directory or object-store prefix. Keep each Run bounded by making the prefix itself the unit of scheduling.

Good partition keys:

- time: `dt=YYYY-MM-DD/hour=HH`
- source: `stripe/`, `salesforce/`, `orders/`
- tenant or region: `tenant=acme/`, `region=us/`
- workload size: `large/`, `small/`, `backfill/`

Example scheduler shape:

```
00:05  filedge run --dir s3://landing/orders/dt=2026-05-24/hour=00/ ...
01:05  filedge run --dir s3://landing/orders/dt=2026-05-24/hour=01/ ...
02:05  filedge run --dir s3://landing/orders/dt=2026-05-24/hour=02/ ...
```

This avoids repeatedly listing a huge bucket prefix and keeps retries localized to the partition where a failure happened.

## Compact small files

Many small files are expensive because each File has its own hash, audit record, destination write, and commit. If an upstream system produces event-sized or page-sized NDJSON files, compact them first.

```bash
filedge compact \
  --watched-dir s3://landing/events/dt=2026-05-24/hour=00/ \
  --output s3://landing/events-compacted/dt=2026-05-24/hour=00/ \
  --max-files 5000 \
  --compress
```

Then load the compacted prefix:

```bash
filedge run \
  --dir s3://landing/events-compacted/dt=2026-05-24/hour=00/ \
  --config pipeline.yaml \
  --audit-db-url "$FILEDGE_AUDIT_DB_URL"
```

See [Compact small files](compact.md) for manifest mode, delete-source mode, and crash recovery.

## Parallel workers

Parallel workers are safe when they share an Audit DB and the destination connector can handle concurrent writes. Each worker tries to claim `PENDING` files by Content Hash; only one worker can move a File to `PROCESSING`.

Prefer one of these shapes:

### Parallel by prefix

Run one worker per partition:

```bash
filedge run --dir s3://landing/orders/dt=2026-05-24/hour=00/ --config pipeline.yaml
filedge run --dir s3://landing/orders/dt=2026-05-24/hour=01/ --config pipeline.yaml
filedge run --dir s3://landing/orders/dt=2026-05-24/hour=02/ --config pipeline.yaml
```

This is the easiest mode to operate because workers do not repeatedly scan the same files.

### Parallel by file pattern

If files must land in one flat prefix, shard them by filename and run non-overlapping patterns:

```yaml
# pipeline-a.yaml
file_pattern: "orders_a*.ndjson"
```

```yaml
# pipeline-b.yaml
file_pattern: "orders_b*.ndjson"
```

Then schedule both:

```bash
filedge run --dir s3://landing/orders/ --config pipeline-a.yaml
filedge run --dir s3://landing/orders/ --config pipeline-b.yaml
```

Use file patterns only when they are truly non-overlapping. Overlap is still protected by the Audit DB, but each worker pays the scan and hash cost for files it can see.

### Parallel against the same prefix

Multiple workers can point at the same `--dir` and the same `pipeline.yaml`, but this is usually less efficient because each worker scans and hashes the same files before claiming work. Use it only when destination write time dominates scan time.

When doing this:

- use PostgreSQL for the Audit DB
- set `stale_timeout_minutes` longer than the slowest normal File load
- use `--no-progress --log-format json --json` in schedulers
- watch destination connector limits, quotas, and table locks

## Large files

Filedge streams rows and writes them in batches, so memory is bounded by `batch_size`, not full file size.

Start with:

```yaml
batch_size: 5000
stale_timeout_minutes: 180
```

Tune from there:

- increase `batch_size` when database round trips dominate and memory is stable
- decrease `batch_size` when workers use too much memory
- split very large upstream exports into several complete files if one file takes longer than the desired retry window
- avoid making files so small that destination transaction overhead dominates

As a rule of thumb, aim for files that take minutes, not hours, to load. Long-running files reduce parallelism and make stale-lock tuning harder.

## Connector guidance

| Destination | Parallel guidance |
|-------------|-------------------|
| PostgreSQL | Good for parallel workers; monitor table indexes, locks, and connection limits |
| BigQuery | Good for parallel bulk loads; monitor load-job quotas and the 7-day job ID idempotency window |
| Databricks | Good for staged warehouse loads; monitor warehouse concurrency and staging throughput |
| DuckDB | Single-writer; run one worker per destination file |
| SQLite destination | Single-writer; use for local dev or small single-worker deployments |

The Audit DB can be SQLite for local single-worker runs. Use PostgreSQL as the Audit DB when multiple workers run at the same time.

## Operational checklist

Before increasing parallelism:

- `filedge healthcheck` passes for the Audit DB and destination connector
- prefixes are bounded by time or another partition key
- `stale_timeout_minutes` is greater than the expected slowest File load
- workers emit JSON logs and JSON run summaries
- `filedge status --json` is monitored for `FAILED` and long-lived `PROCESSING` files
- destination quotas, lock behavior, and connection limits are understood

After increasing parallelism:

- compare `files_scanned`, `committed`, `failed`, `rows_committed`, and `duration_s` from `--json`
- watch for repeated retries of the same Content Hash
- watch destination-side latency and throttling
- reduce worker count if scan cost or destination contention grows faster than committed throughput

## Backfills

For backfills, keep historical data isolated from the normal ingestion path:

```
s3://landing/orders-backfill/dt=2025-01-01/
s3://landing/orders-backfill/dt=2025-01-02/
```

Run backfill workers with their own schedule and a conservative worker count. They may share the same Audit DB and destination table as normal ingestion; Content Hash idempotency still prevents duplicate file loads.

If the backfill uses a different schema or destination table, use a separate `pipeline.yaml`.

## Related

- [Run a pipeline](run.md) — the command being scaled
- [Compact small files](compact.md) — fewer, larger files before ingestion
