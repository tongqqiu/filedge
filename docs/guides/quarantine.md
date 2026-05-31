# Quarantine bad rows

By default Filedge runs in Strict Mode: if one row fails validation, the **whole
File fails** and nothing is committed (ADR-0003). Dead-Letter Quarantine (ADR-0019)
is an opt-in relaxation for the case where a handful of rows in an otherwise-good
partner File are malformed: the good rows commit, the bad rows are set aside in an
NDJSON **quarantine sidecar** for inspection and re-drop, and the File still reaches
the terminal `COMMITTED` state — recording both how many rows landed and how many
were quarantined, so the partial is never silent.

A **failure threshold** keeps this honest: if too many rows are bad, the File fails
wholesale (nothing committed, no sidecar) exactly like Strict Mode. A few stragglers
quarantine; a systemically broken File still fails loudly.

## Enable quarantine

Quarantine is off by default and opt-in per Pipeline. Add a `quarantine:` block to
`pipeline.yaml`:

```yaml
quarantine:
  enabled: true
  dir: ./quarantine            # where sidecars are written
  max_invalid_fraction: 0.01   # fail the File if >1% of rows are bad
  max_invalid_rows: 500        # ...or if more than 500 rows are bad
```

At least one threshold (`max_invalid_fraction`, between 0 and 1, and/or
`max_invalid_rows`) is required. A File is over-threshold — and fails wholesale — if
it exceeds **either** limit.

> **Tip:** always set `max_invalid_rows` as well as a fraction. A fraction-only
> threshold on a very large File can still legally quarantine a large number of rows
> (1% of 10M is 100K); an absolute cap bounds the sidecar.

## Find a quarantine sidecar

When a File partially commits, `filedge status` reports the quarantined total:

```
PENDING:    0
PROCESSING: 0
COMMITTED:  42
FAILED:     0
QUARANTINED ROWS: 7
```

The exact sidecar path is recorded on the File's Audit Record and shown in the
[Audit Export](audit-export.md) (a quarantined File is badged distinctly from a clean
`COMMITTED` File, with its row count and sidecar path in the row detail).

Sidecars are named after the source File and its Content Hash, so they correlate
back to the Audit Record:

```
./quarantine/orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson
```

Each line is one quarantined row with diagnostics — the offending row number,
column, error, and the original raw row:

```json
{"row_number": 12, "column": "amount", "error": "cannot coerce 'n/a' to float", "row": {"id": "12", "amount": "n/a"}}
{"row_number": 88, "column": "amount", "error": "cannot coerce '' to float", "row": {"id": "88", "amount": ""}}
```

## Investigate a sidecar

A sidecar is plain NDJSON, so you can query it directly — no need to load it anywhere
first. Two tools cover almost everything: `jq` for quick filtering, and DuckDB for
aggregation that scales to large sidecars.

### Quick triage with `jq`

Count the rows and skim the errors:

```bash
# How many rows were quarantined?
wc -l orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson

# What went wrong, one line each?
jq -r '"\(.row_number)\t\(.column)\t\(.error)"' \
  orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson

# Just the offending column and the raw value it choked on
jq -r '"\(.column)=\(.row[.column])"' \
  orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson
```

### Aggregate with DuckDB

DuckDB reads NDJSON natively and handles sidecars with millions of rows comfortably —
so even a large sidecar is queryable in a one-liner.

> These examples use the standalone **DuckDB CLI** (`brew install duckdb`, or see
> [duckdb.org/docs/installation](https://duckdb.org/docs/installation/)). Filedge's
> `duckdb` extra installs the *Python library*, not the CLI — if you have that
> instead, run the same SQL via `python -c "import duckdb; print(duckdb.sql('''…'''))"`.
>
> Note that `column` is a SQL reserved word, so the sidecar's `column` field must be
> quoted as `"column"` in every query below.

Group the failures by column to see where the data quality problem actually is:

```bash
duckdb -c "
  SELECT \"column\", count(*) AS bad_rows
  FROM read_json_auto('orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson')
  GROUP BY \"column\"
  ORDER BY bad_rows DESC;
"
```

```
┌─────────┬──────────┐
│ column  │ bad_rows │
├─────────┼──────────┤
│ amount  │      6   │
│ id      │      1   │
└─────────┴──────────┘
```

Cluster by the error message to distinguish a few stragglers from a systemic issue:

```bash
duckdb -c "
  SELECT error, count(*) AS n
  FROM read_json_auto('orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson')
  GROUP BY error ORDER BY n DESC;
"
```

Inspect specific bad rows — the raw row is nested under `row`:

```bash
duckdb -c "
  SELECT row_number, row.id AS id, row.amount AS amount, error
  FROM read_json_auto('orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson')
  WHERE \"column\" = 'amount'
  LIMIT 20;
"
```

Sweep an entire quarantine directory at once with a glob, to see which Files are
quarantining the most:

```bash
duckdb -c "
  SELECT filename, count(*) AS bad_rows
  FROM read_json_auto('./quarantine/*.quarantine.ndjson', filename = true)
  GROUP BY filename ORDER BY bad_rows DESC;
"
```

## Re-drop corrected rows

Once you understand what failed, fix the values and re-ingest. The sidecar itself is
**not** directly re-droppable — the real data is nested under `row`, wrapped in the
diagnostic fields — so `filedge redrop-quarantine` unwraps it back into a clean
NDJSON File you can correct and drop:

```bash
filedge redrop-quarantine \
  --sidecar ./quarantine/orders-2026-03-15.a1b2c3d4e5f6.quarantine.ndjson
```

```
Wrote 7 row(s) to ./quarantine/orders-2026-03-15.a1b2c3d4e5f6.redrop.ndjson
```

The output is the quarantined rows with the `row_number`/`column`/`error`
diagnostics stripped — just the source columns, as NDJSON. Edit the bad values,
then drop the corrected File into the Watched Directory and run the pipeline as
normal:

```bash
# correct the values in the .redrop.ndjson file, then:
mv orders-2026-03-15.a1b2c3d4e5f6.redrop.ndjson ./incoming/
filedge run --dir ./incoming --config pipeline.yaml
```

Because the corrected File has new content, it ingests under a **new Content Hash**
(ADR-0002) — the original quarantined File's Audit Record is preserved, and the
correction is a distinct, traceable load.

The re-dropped File is always NDJSON. If your Pipeline's declared format is CSV,
Excel, or fixed-width, point an NDJSON-accepting Pipeline at the corrected File
(`filedge redrop-quarantine` warns you when you pass `--pipeline`/`--config` for a
non-NDJSON Pipeline).

## Typical workflow

```
1. filedge status                  → spot QUARANTINED ROWS, find the File
2. <inspect sidecar>               → jq / DuckDB to see what failed and how widely
3. filedge redrop-quarantine       → unwrap the sidecar into a clean NDJSON File
4. <correct the bad values>        → fix the data in the re-dropped File
5. filedge run                     → corrected rows ingest under a new Content Hash
```

## Options

`filedge redrop-quarantine`:

| Option | Description |
|--------|-------------|
| `--sidecar` | (required) Path to the quarantine sidecar to re-drop. |
| `--output` | Where to write the clean NDJSON File. Default: alongside the sidecar as `<name>.redrop.ndjson`. |
| `--pipeline` | Pipeline Registry id to check NDJSON re-drop compatibility against (warns if not NDJSON). |
| `--workspace` | Workspace root holding `pipeline-registry.yaml` (used with `--pipeline`). |
| `--config` | Pipeline Config to check NDJSON re-drop compatibility against. Mutually exclusive with `--pipeline`. |

## Related

- [Run a pipeline](run.md) — how files are committed and retried
- [Requeue failed files](requeue.md) — for whole-File failures (vs. row-level quarantine)
- [Export an audit site](audit-export.md) — where quarantined Files are surfaced to audit stakeholders
- [Validate a file](validate.md) — `--config` dry-run previews whether rows would quarantine
