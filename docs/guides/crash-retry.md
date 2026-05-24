# Crash-retry demo

This guide walks through what happens when a worker crashes mid-run and how Filedge automatically recovers on the next invocation without duplicating destination rows.

## Background

When `filedge run` picks up a file it:

1. Marks the file `PROCESSING` in the Audit DB (acts as a distributed lock).
2. Streams rows to the destination via the Connector (idempotent per `file_hash`).
3. Marks the file `COMMITTED` in the Audit DB.

If the process dies between steps 1 and 3, the file is left `PROCESSING`. On the next run, Filedge reclaims any `PROCESSING` lock older than `stale_timeout_minutes` (default: 30) and re-queues the file as `PENDING`. Because the Connector's `write_rows` is idempotent — it deletes existing rows for that `file_hash` before inserting — the retry produces the same destination state regardless of how far the first attempt progressed.

## Local walkthrough

### 1. Set up a pipeline

```bash
mkdir -p /tmp/crash-demo/incoming

cat > /tmp/crash-demo/incoming/orders.csv <<'EOF'
order_id,amount
1001,49.99
1002,149.00
EOF

cat > /tmp/crash-demo/pipeline.yaml <<'EOF'
format: csv
dest_table: orders
write_mode: append
retry_cap: 3
stale_timeout_minutes: 30
connector:
  type: sqlite
  url: sqlite:///orders.db
columns:
  - { source: order_id, dest: order_id, type: string, required: true }
  - { source: amount,   dest: amount,   type: float,  required: true }
EOF
```

### 2. Run normally

```bash
cd /tmp/crash-demo
filedge run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///filedge.db
# Committed: 1  Failed: 0  Skipped: 0  New: 1  Reclaimed: 0  Retried: 0
```

### 3. Simulate a crash

Force the file back to `PROCESSING` with a timestamp old enough to trigger reclaim.
This simulates a worker that claimed the file and then died.

```bash
python3 - <<'EOF'
import sqlite3, datetime
db = sqlite3.connect("/tmp/crash-demo/filedge.db")
stale_ts = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
db.execute(
    "UPDATE etl_file_audit SET state='PROCESSING', claimed_at=? WHERE state='COMMITTED'",
    [stale_ts],
)
db.commit()
EOF
```

Confirm the simulated state:

```bash
filedge status --audit-db-url sqlite:///filedge.db
# PENDING:    0
# PROCESSING: 1
# COMMITTED:  0
# FAILED:     0
```

### 4. Re-run Filedge

```bash
filedge run --dir ./incoming --config pipeline.yaml --audit-db-url sqlite:///filedge.db
# Committed: 1  Failed: 0  Skipped: 0  New: 0  Reclaimed: 1  Retried: 0
```

The `Reclaimed: 1` counter confirms the stale lock was recovered.

### 5. Verify no duplication

```bash
python3 - <<'EOF'
import sqlite3
rows = sqlite3.connect("/tmp/crash-demo/orders.db").execute("SELECT COUNT(*) FROM orders").fetchone()[0]
print(f"Destination rows: {rows}")  # expect 2
EOF
```

The destination has exactly two rows — the same as after the first run.
The connector deleted the rows for that `file_hash` before re-inserting, so no duplicates were created.

### 6. Verify the final audit state

```bash
filedge status --audit-db-url sqlite:///filedge.db
# PENDING:    0
# PROCESSING: 0
# COMMITTED:  1
# FAILED:     0
```

## What to take away

| Scenario | Outcome |
|---|---|
| Crash before connector writes | Retry writes rows normally |
| Crash after connector writes, before audit COMMITTED | Retry deletes existing rows, re-inserts — same final state |
| Crash after audit COMMITTED | Next run skips the file (content hash already COMMITTED) |

The key guarantee: **a retry always produces the same destination state as a first successful write**.

## Related

- [Run a pipeline](run.md) — full `filedge run` reference including stale lock configuration
- [Requeue failed files](requeue.md) — manually reset terminal-FAILED files
