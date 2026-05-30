# File formats

Filedge detects the format from the file extension (or the `format:` field in
`pipeline.yaml`). Every read command — `inspect`, `preview`, `validate`,
`run` — works the same way across formats.

| Format | `format:` value | Extension | Install |
|--------|-----------------|-----------|---------|
| CSV | `csv` | `.csv` | built-in |
| NDJSON | `ndjson` | `.ndjson` | built-in |
| Parquet | `parquet` | `.parquet` | `uv sync --extra parquet` |
| Excel | `excel` | `.xlsx` | `uv sync --extra excel` |
| Fixed-width | `fixed_width` | (any) | built-in |

NDJSON is the canonical interchange format — companions such as `filedge-fetch`
and `filedge-materialize` always materialize complete NDJSON Files.

## Parquet

Install the optional extra, then point any read command at a `.parquet` file:

```bash
uv sync --extra parquet
filedge inspect events.parquet
filedge validate events.parquet --config pipeline.yaml
```

## Excel

Install the `excel` extra. The first sheet is read by default; use
`--sheet <name-or-index>` to choose another:

```bash
uv sync --extra excel
filedge inspect data.xlsx
filedge preview data.xlsx --sheet Orders
```

See the [Inspect guide](inspect.md#excel-files) for the formula-cache and
leading-zeros gotchas. Legacy `.xls` is not supported — re-save as `.xlsx` first.

## Fixed-width

Fixed-width files carry no header or separator, so the column layout must be
declared in `pipeline.yaml`. See the dedicated
[Fixed-width files](fixed-width.md) guide.

---

**Related:** [Getting Started](../getting-started.md) ·
[pipeline.yaml reference](../reference/pipeline-yaml.md) ·
[Column types](../reference/column-types.md)
