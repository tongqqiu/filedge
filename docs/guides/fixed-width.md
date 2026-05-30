# Fixed-width files

Fixed-width files have no separator and no header — every record is one line, every field occupies a known byte range. Common with legacy partner extracts (transaction reports, position files, IRS-style records, billing runs).

Because the layout lives entirely outside the file, fixed-width is the only Filedge format where `pipeline.yaml` is *required* to read even one row. The partner record-layout spec is the source of truth; Filedge mechanically applies it.

See [ADR-0013](../adr/0013-fixed-width-format-support.md) for the architectural decisions captured here.

## Declaring a layout

Add `format: fixed_width` and inline `start:`/`width:` on each entry in `columns:`:

```yaml
format: fixed_width
dest_table: transactions

columns:
  - source: account_number
    dest: account_number
    type: string
    required: true
    start: 1
    width: 10
  - source: transaction_date
    dest: transaction_date
    type: date
    required: true
    start: 11
    width: 8
  - source: amount
    dest: amount_cents
    type: integer
    required: true
    start: 19
    width: 12
```

- `start:` is **1-indexed**, matching the convention used in partner record-layout specs. The first byte of a line is `start: 1`.
- `width:` is the number of bytes the column occupies.
- **Filler bytes between columns are allowed** — declare only the columns you care about. Skipped bytes pass through silently.
- Columns must be **declared in sorted order by `start`** and **must not overlap**.

## Runtime behavior

- **Whitespace is stripped** from both sides of every extracted value before type coercion. Left-padded numbers and right-padded strings produce the cleaned value you expect.
- **Blank and whitespace-only lines are skipped** silently. Trailing newlines and record-section separators are not errors.
- **Lines longer than the declared layout** are ingested cleanly — trailing bytes are silently ignored. This matches Filedge's Column Tolerance principle for CSV.
- **Lines shorter than the declared layout** fail the entire File under Strict Mode (ADR-0003). No records are committed when a line is truncated.
- **Encoding is hardcoded to UTF-8**. EBCDIC and other legacy encodings are out of v1 scope — preprocess upstream.

## Validation errors

Filedge validates the layout at YAML load time and gives column-named feedback so you can fix mistakes in seconds:

```
Error: Columns 'account_number' (start=1, width=10) and 'branch_code' (start=8, width=4)
overlap at byte positions 8-10. Each byte must belong to at most one column.
```

Runtime errors name the offending byte position and column too:

```
File landing/transactions-2026-05-25.fwf failed: line 47 is 24 bytes long but the
declared layout requires at least 30 bytes (last column 'amount' ends at byte 30).
No records were committed.
```

## CLI workflow

Fixed-width is the one format where `filedge inspect` does **not** work — there is no embedded schema to infer:

```
$ filedge inspect transactions.fwf --format fixed_width
Error: filedge inspect does not support fixed_width — the layout is not discoverable
from the file. Declare it from your partner record-layout spec following
docs/guides/fixed-width.md.
```

Once you've written the layout, the standard workflow is:

```bash
# 1. Preview rows applying your layout
filedge preview transactions.fwf --format fixed_width --config pipeline.yaml

# 2. Validate the file against the full pipeline.yaml
filedge validate transactions.fwf --format fixed_width --config pipeline.yaml

# 3. Drop the file into the Watched Directory and run
filedge run --dir landing/ --config pipeline.yaml --audit-db-url sqlite:///audit.db
```

Both `preview` and `validate` require `--config` for fixed-width — without a layout, there's no rows to render.

## What's out of scope (v1)

These are deferred per ADR-0013 until a real operator case appears:

- Multi-record-type files (NACHA ACH, BAI2). Preprocess upstream into per-record-type files.
- COBOL numeric formats (implicit decimal, signed overpunch, packed decimal).
- EBCDIC and other non-UTF-8 encodings (`cp037`, `cp1047`, etc.).
- Fixed-record-length files without newlines.
- Configurable trim behavior — Filedge always `.strip()`s both sides.
- A `filedge ruler` byte-position helper subcommand.

## Related

- [File formats](file-formats.md) — the full format matrix
- [Inspect a file](inspect.md) — generate a starting columns block
- [Column types](../reference/column-types.md) — coercion after byte extraction
