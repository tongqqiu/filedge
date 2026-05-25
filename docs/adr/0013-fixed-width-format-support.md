# Fixed-width (`format: fixed_width`) is a Parser format, with schema declared in pipeline.yaml

Filedge will add fixed-width text files as the next Parser format after Excel (ADR-0012). The driving case is anticipated: fintech operators routinely receive single-record-type fixed-width files from legacy partners and mainframe-derived extracts. Today the workaround is to preprocess into CSV upstream; absorbing that into Filedge keeps the audit boundary at the original File and removes a brittle, schema-duplicated conversion step.

This addition is architecturally different from every previous Parser. CSV, NDJSON, Parquet, and Excel all carry their schema in-band — separators, line-delimited JSON, embedded Parquet schema, or row-1 headers in a sheet. Fixed-width has **no separator and no embedded schema**. The column layout is *entirely external* to the file. This is the design constraint that drives every decision below.

## Decision

`format: fixed_width` is a Parser format. The column layout (positions and widths) is declared in `pipeline.yaml` as additional fields on each entry in the existing `columns:` block. The partner's record-layout spec is the source of truth; Filedge mechanically applies it.

### Scope of v1

- **Position model**: `start` (1-indexed, matching partner-spec convention) + `width`, declared inline on each entry in `columns:`. No `end`-based ranges, no width-only positional declarations. Gaps between columns are allowed (filler bytes). Columns must be sorted by `start` and must not overlap.
- **Encoding**: hardcoded UTF-8. Bytes-identical to ASCII for the anticipated case. No `encoding:` config field in v1.
- **Whitespace**: hardcoded `.strip()` (both sides) on every extracted value. No `trim:` config field in v1.
- **Record terminator**: newline-delimited only (`\n` and `\r\n`).
- **Single record type per file**. Multi-record-type files (NACHA ACH, BAI2) must be preprocessed upstream into per-record-type files before reaching the Watched Directory.
- **`filedge inspect` is not supported** for fixed-width — it returns a clear error pointing at the docs. Schema Inference is architecturally infeasible: without an externally-declared layout, the file cannot be parsed at all.
- **`filedge preview` and `filedge validate`** require `--config <pipeline.yaml>` for fixed-width. The same "no layout, no preview" rule applies.
- **Numeric coercion** uses the existing `coerce_value` pipeline. Extracted bytes are `.strip()`'d, then handed to `int()`, `float()`, ISO date parsing, etc. No new numeric handling.

### Validation behavior

At pipeline.yaml load time:

- `width <= 0` → reject
- `start < 1` → reject
- Overlapping columns → reject (catastrophic-typo prevention; e.g. `start=1, width=10` and `start=8, width=4` overlap at bytes 8–10)
- Columns not sorted by `start` → reject (no auto-sort; the human-readable order is part of the contract)
- Gaps between columns (filler bytes) → allow without warning

At runtime:

- Blank or whitespace-only line → skip silently
- Line shorter than `max(start + width - 1)` → fail the File (Strict Mode, per ADR-0003)
- Line longer than `max(start + width - 1)` → OK, trailing bytes silently ignored (consistent with the Column Tolerance principle in CONTEXT.md)

### YAML shape

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

No peer `fixed_width:` block in v1 — every globally-relevant option (encoding, trim) is hardcoded. The block can be introduced later when a real case justifies it.

### Architectural divergence: schema-external

Fixed-width is the first Filedge format where the schema lives entirely in `pipeline.yaml` and not at all in the file. This drives three things worth pinning:

1. **The Parser depends on YAML.** Other Parsers can produce rows from a file with no config (`inspect` exercises this for CSV/NDJSON/Parquet/Excel). `FixedWidthParser` cannot. `get_parser` becomes a factory accepting per-format configuration (mirroring how `ExcelParser` accepts `sheet`).
2. **`filedge inspect` is not universal.** Every prior format supports inspect-without-config. For fixed-width, inspect hard-errors. This is a deliberate UX boundary — the docs page tells the operator to declare the layout from the partner spec first.
3. **The CONTEXT.md glossary gains a new term — "Fixed-Width Layout"** — recording that the layout is the only way to parse the format. This pins the constraint against future agents who might try to add inference.

## Considered options

- **Fixed-width as a Parser with inline `start`+`width` (this decision)** — matches partner-spec conventions, allows filler-byte gaps naturally, leans on existing Strict Mode for runtime errors. Narrow v1 keeps the surface honest.
- **`start`+`end` range form** — rejected because half-open vs. closed-range is a permanent off-by-one footgun on the operator interface.
- **`width`-only positional declarations** — rejected because filler bytes would require phantom columns, and a single typo silently shifts every downstream column.
- **`filedge ruler` byte-position helper subcommand** — deferred. Real ergonomic value but out of v1 scope; reopen when an operator hits "I'm hand-counting characters" friction.
- **Schema-inference for fixed-width** — architecturally infeasible without parsing a partner-spec document (PDF/Excel/etc.). Out of scope, and not just "for v1" — out of scope as a concept.
- **Multi-record-type files (NACHA ACH, BAI2) as a single ingestion** — would break the Content Hash + one-file-one-table invariant the system depends on. Upstream preprocessing splits these into per-record-type files; Filedge ingests the result. Same shape as ADR-0007's Queue Materializer pattern.
- **EBCDIC and other non-UTF-8 encodings** — deferred; no anticipated case in v1. Reopen if a real legacy partner case appears.
- **COBOL numeric formats (implicit decimal, signed overpunch, packed decimal)** — deferred; operators with these fields preprocess upstream. Same posture as the multi-record-type case.
- **Fixed-record-length without newlines** — deferred; most ASCII fixed-width files are newline-delimited.

This is the third instance (after ADRs 0005/0006/0007 and ADR-0012) of the recurring principle: scope follows real target-user evidence, the boundary stays at the File, and upstream transforms own format-conversion concerns Filedge does not.
