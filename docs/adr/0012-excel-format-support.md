# Excel (`.xlsx`) is the next Parser format, ahead of Avro

Filedge will add Excel (`.xlsx`) as the next file format alongside CSV, NDJSON, and Parquet. The Parser glossary entry in CONTEXT.md previously named Avro as the next candidate; this ADR records the decision to go off that roadmap.

The driving evidence is real: business users at the target customer profile (data engineering teams at fintech companies — CONTEXT.md) are landing small datasets as `.xlsx` files. Today the workaround is "convert to CSV first." Avro, in contrast, is hypothetical here — Kafka/Hadoop-ecosystem use is already covered by the Queue Materializer pattern (ADR-0007), which materializes records as complete NDJSON files in the Watched Directory before `filedge run` sees them. Avro stays a candidate, not removed from consideration, but its real-case bar has not been met.

This is the same principle that drove ADR-0005 (SFTP out of scope), ADR-0006 (API sources fetched to files), and ADR-0007 (queue sources materialized to files): scope follows real target-user evidence, not an abstract data-engineering roadmap. Recording the principle once more here so a future "should we add format X?" question lands on the established bar rather than re-litigating the trade-off.

## Scope of the Excel Parser

The initial implementation is deliberately narrow, sized to the driving "small business datasets" case:

- **Library**: `openpyxl`, loaded with `read_only=True, data_only=True`. Pure-Python install, no compiled toolchain in CI. Optional extra: `uv sync --extra excel`.
- **Extension**: `.xlsx` only. `.xls` (legacy binary) returns `FormatNotDetected` — the same shape as the existing rejection of plain `.json`. Users with `.xls` save as `.xlsx` in Excel as a one-click upstream fix.
- **Multi-sheet workbooks**: first sheet by default. `--sheet <name-or-index>` overrides on `filedge inspect`/`preview`/`validate`. A warning prints to stderr when the workbook has more than one sheet. Content Hash semantics are unchanged — one file's bytes remain one Content Hash. A workbook with multiple logical tables must be split into per-sheet files upstream.
- **Pipeline config**: `pipeline.yaml` grows a peer `excel:` block when `format: excel`. The `sheet:` subkey is required for production runs (no silent first-sheet defaulting in `filedge run`). The block mirrors the existing `connector:` block precedent.
- **Header row**: row 1 only. Spreadsheets with title rows or footer totals must be cleaned in Excel before save. A `--header-row` flag is an explicit follow-up if real Pattern-2 cases land.
- **Type handling**: cells are coerced to strings on yield (`datetime` → `.isoformat()`, `bool`/`int`/`float` → `str(v)`, `None` → `None`). Schema inference behaves identically to CSV — same Confidence Tier output, no inferrer changes required. This preserves the `filedge inspect` UX users already know from the convert-to-CSV workaround.
- **Formulas**: `data_only=True` returns cached computed values. Workbooks edited and never reopened in Excel may have a stale or absent formula cache; documented as a known limitation.
- **Leading zeros**: cells stored as numbers lose leading zeros (e.g. zip codes). Mitigation is the same as for CSV — operators format the column as Text in Excel before save. Documented.

## Out of scope for the initial change

- `--header-row` / `--skip-trailing-rows` flags. Add only when real Pattern-2 (title-row) or Pattern-3 (footer-totals) cases appear.
- `.xls`, `.xlsb`, `.ods` support. Would require swapping `openpyxl` for `python-calamine`; reopen if a real legacy-format case appears.
- Multi-sheet workbook → multi-file logical model. Would require a Content Hash redesign — its own ADR if ever.
- Native-type schema inference. The inferrer at `filedge/inferrer.py:112` requires string inputs and demotes typed values to "ambiguous string" — a pre-existing wart that also affects NDJSON. Fixing it benefits both formats and deserves a separate decision.
- A `filedge xlsx-to-csv` conversion utility. Same "use upstream tooling" principle as ADR-0005.

## Considered options

- **Excel via openpyxl, narrow v1 (this decision)**: matches the real driving case, minimal new surface area, clean follow-up path if richer behavior is needed.
- **Avro first, defer Excel**: follows the previously documented roadmap, but Avro lacks real-case evidence today and Excel does not. Rejecting on the same evidence bar that rejected general JSON.
- **Excel via python-calamine**: faster, broader format support (`.xls`/`.xlsb`/`.ods`), but adds a Rust-wheel dependency for a perf benefit that the small-dataset case never feels. Reopen if `.xls` becomes a real case.
- **Sheets-as-files multi-table model**: maximally expressive but breaks the "Content Hash = file bytes" invariant the system depends on for idempotency. Out of scope.
