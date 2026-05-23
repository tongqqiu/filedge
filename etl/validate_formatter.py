from etl.validator import ValidationResult

_MAX_WIDTH = 80


def format_text(result: ValidationResult) -> str:
    lines = []
    if result.undeclared_columns:
        cols = ", ".join(result.undeclared_columns)
        lines.append(f"⚠ undeclared columns will be ignored: {cols}")
    for f in result.failures:
        line = f"✗ row {f.row_number}  {f.column}  {f.error}"
        if len(line) > _MAX_WIDTH:
            line = line[:_MAX_WIDTH - 1] + "…"
        lines.append(line)
    if result.failures:
        lines.append(
            f"{len(result.failures)} failure(s) in {result.rows_checked} rows checked"
        )
    else:
        lines.append(f"✓ {result.rows_checked} rows checked, no failures")
    return "\n".join(lines)


def format_json(result: ValidationResult) -> dict:
    return {
        "rows_checked": result.rows_checked,
        "failures": [
            {"row": f.row_number, "column": f.column, "error": f.error}
            for f in result.failures
        ],
        "undeclared_columns": result.undeclared_columns,
    }
