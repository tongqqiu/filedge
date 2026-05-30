# Requeue terminal-FAILED files

`filedge requeue` resets terminal-FAILED files back to `PENDING` so they are retried on the next `filedge run`. Use it after fixing the root cause — bad source data, a schema mismatch, a transient connector error, or anything else that caused the file to exhaust its retry budget.

## Background: terminal vs non-terminal failures

A file that fails is marked `FAILED` with `attempt_count = 1`. On the next run, `filedge run` automatically resets it to `PENDING` for retry, up to `retry_cap` attempts (default: 3).

Once `attempt_count >= retry_cap`, the file is **terminal** — `filedge run` no longer retries it automatically. It stays `FAILED` and shows as `Skipped` in the run summary. Terminal files require explicit operator action.

`filedge requeue` is that action. It resets `state=PENDING`, `attempt_count=0`, and clears `error_message`, giving the file a full fresh retry budget.

## Requeue a single file

```bash
filedge requeue orders-2026-03-15.csv \
  --audit-db-url sqlite:///filedge.db
```

Use `filedge status` first to find the failing filename:

```
Recent failures:
  orders-2026-03-15.csv  a1b2c3d4e5f6...  cannot coerce 'n/a' to float (row 12, column: amount)
```

On success:

```
Requeued: orders-2026-03-15.csv (a1b2c3d4e5f6…)
```

### Disambiguating duplicate filenames

If the same filename appears twice in the audit log (two different content hashes), `filedge requeue` will error and list both candidates:

```
Error: 2 terminal-FAILED records found for 'orders.csv'. Use --hash to disambiguate:
  --hash a1b2c3d4e5f6...  (error: cannot coerce 'n/a' to float)
  --hash 9f8e7d6c5b4a...  (error: missing required column 'amount')
```

Pick the one you want and pass `--hash`:

```bash
filedge requeue orders.csv \
  --hash a1b2c3d4e5f6... \
  --audit-db-url sqlite:///filedge.db
```

## Requeue all terminal failures

After a systemic incident — a connector outage, a schema change, a bad batch of files — you may want to reset everything at once.

### Preview what would change (no writes)

```bash
filedge requeue --all-terminal-failed --dry-run \
  --audit-db-url sqlite:///filedge.db
```

```
  orders-2026-03-15.csv  a1b2c3d4e5f6...  cannot coerce 'n/a' to float
  orders-2026-03-16.csv  b2c3d4e5f6a7...  cannot coerce 'n/a' to float
  orders-2026-03-17.csv  c3d4e5f6a7b8...  cannot coerce 'n/a' to float

Would requeue 3 file(s). Re-run with --yes to proceed.
```

### Check how many are affected (no writes)

```bash
filedge requeue --all-terminal-failed \
  --audit-db-url sqlite:///filedge.db
```

```
Found 3 terminal-FAILED file(s). Re-run with --yes to requeue.
```

This exits non-zero so it is safe to call from a script to detect stuck files without accidentally resetting anything.

### Execute the bulk reset

```bash
filedge requeue --all-terminal-failed --yes \
  --audit-db-url sqlite:///filedge.db
```

```
Requeued: 3
```

Then run the pipeline to process them:

```bash
filedge run --dir ./incoming --config pipeline.yaml
```

## Typical recovery workflow

```
1. filedge status             → spot terminal FAILED files and their errors
2. fix the root cause         → patch source data, fix schema, restore connector
3. filedge requeue <file>     → reset the file (or --all-terminal-failed --yes for bulk)
4. filedge run                → file is processed normally on the next run
```

## Non-terminal failures do not need requeue

Files with `attempt_count < retry_cap` are still eligible for automatic retry. `filedge run` resets them to `PENDING` at the start of each run — no manual action needed.

## Options

| Option | Description |
|--------|-------------|
| `filename` | (positional) Filename to requeue. Mutually exclusive with `--all-terminal-failed`. |
| `--hash` | Content hash to disambiguate when multiple audit records share the same filename. |
| `--all-terminal-failed` | Requeue all terminal-FAILED files. Mutually exclusive with `filename`. |
| `--dry-run` | List files that would be requeued without making changes. Requires `--all-terminal-failed`. |
| `--yes` | Confirm bulk requeue. Required to execute `--all-terminal-failed`. |
| `--retry-cap` | Retry cap used to identify terminal-FAILED files (default: `3`). Must match `pipeline.yaml`. |
| `--audit-db-url` | Audit database URL. Defaults to `$FILEDGE_AUDIT_DB_URL`. |

## Related

- [Run a pipeline](run.md) — retries happen on the next run
- [Crash-safe retry](crash-retry.md) — automatic recovery vs. manual requeue
