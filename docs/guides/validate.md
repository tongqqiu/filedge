# Validate a file

`filedge validate` dry-runs a file against a `pipeline.yaml` config and reports every row that would fail type coercion or violate a `required: true` constraint. **No data is written.** No destination connection is opened.

Use it to catch data quality issues before running the full pipeline — especially useful in CI or before loading a large file.

## Basic usage

```bash
filedge validate data.csv --config pipeline.yaml
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | All rows passed |
| `1` | One or more row failures found |
| `2` | Error opening the file or loading the config |

This makes it composable in shell scripts:

```bash
filedge validate data.csv --config pipeline.yaml && filedge run ...
```

## Output

Failures are printed to **stderr** in a compact format:

```
✗ row 42  amount  cannot coerce 'n/a' to float
✗ row 87  amount  cannot coerce '' to float (required)
2 failure(s) in 1000 rows checked
```

A clean file:

```
✓ 1000 rows checked, no failures
```

### Undeclared columns

Columns present in the file but not declared in `pipeline.yaml` produce a warning, not a failure:

```
⚠ undeclared columns will be ignored: internal_ref, legacy_id
✓ 1000 rows checked, no failures
```

These columns are silently skipped at load time — the warning is just a heads-up that you may want to declare them or confirm they're intentionally excluded.

## JSON output

Add `--json` to get machine-readable output suitable for CI reporting or dashboards:

```bash
filedge validate data.csv --config pipeline.yaml --json
```

```json
{
  "rows_checked": 1000,
  "failures": [
    {"row": 42, "column": "amount", "error": "cannot coerce 'n/a' to float"},
    {"row": 87, "column": "amount", "error": "cannot coerce '' to float (required)"}
  ],
  "undeclared_columns": ["internal_ref"]
}
```

The text summary is still printed to stderr; the JSON goes to stdout.

## Sampling

Validate only the first N rows with `--sample-rows`:

```bash
filedge validate data.csv --config pipeline.yaml --sample-rows 100
```

Useful for quick checks on large files. For full pre-load validation, omit `--sample-rows`.

## Excel files

`.xlsx` workbooks are detected from the extension. The sheet to read is taken from the `excel:` block in `pipeline.yaml`:

```yaml
format: excel
excel:
  sheet: Orders
```

Then:

```bash
filedge validate orders.xlsx --config pipeline.yaml
```

!!! note "Excel requires openpyxl"
    ```bash
    uv sync --extra excel
    ```

Use `--sheet <name-or-index>` to validate a different sheet of the same workbook against the same `pipeline.yaml` — useful when one file contains both production and overflow tabs with the same schema:

```bash
filedge validate orders.xlsx --config pipeline.yaml --sheet Overflow
```

The formula-cache and leading-zeros gotchas described in the [inspect guide](inspect.md#excel-files) apply here too. `.xls`, `.xlsb`, and `.ods` are not supported — re-save as `.xlsx`.

## Fixed-width files

For `--format fixed_width`, `filedge validate` reads `start:` and `width:` from the same `pipeline.yaml` it validates against. The standard invocation works unchanged:

```bash
filedge validate transactions.fwf --format fixed_width --config pipeline.yaml
```

See the [fixed-width guide](fixed-width.md) for layout declaration and runtime semantics (short-line behavior, whitespace stripping, blank-line handling).

## Cloud paths

Works with any [fsspec](https://filesystem-spec.readthedocs.io/en/latest/)-supported URI:

```bash
filedge validate s3://my-bucket/landing/data.csv --config pipeline.yaml
filedge validate gs://my-bucket/events.ndjson --config pipeline.yaml
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | required | Path to `pipeline.yaml` |
| `--format` | auto from extension | File format: `csv`, `ndjson`, `parquet`, or `excel` |
| `--sample-rows` | all rows | Validate only the first N rows |
| `--json` | off | Emit JSON to stdout in addition to text summary |
| `--sheet` | from `pipeline.yaml` | Override the `excel:` sheet from `pipeline.yaml` (excel only) |
