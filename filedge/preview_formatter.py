from typing import Any

_MAX_CELL = 30
_MAX_WIDTH = 120


def _truncate(value: str, max_len: int) -> str:
    if len(value) > max_len:
        return value[: max_len - 1] + "…"
    return value


def format_preview(
    rows: list[dict[str, Any]],
    *,
    max_width: int = _MAX_WIDTH,
    max_cell: int = _MAX_CELL,
    start_row: int = 1,
) -> str:
    if not rows:
        return "(no rows)"

    columns = list(rows[0].keys())
    row_num_width = max(1, len(str(start_row + len(rows) - 1)))

    col_widths: dict[str, int] = {}
    for col in columns:
        values = [str(row.get(col) if row.get(col) is not None else "") for row in rows]
        raw = max(len(col), max(len(v) for v in values))
        col_widths[col] = min(raw, max_cell)

    # Select columns that fit within max_width
    # row# column + " | " prefix per subsequent column
    used: list[str] = []
    total = row_num_width
    for col in columns:
        needed = 3 + col_widths[col]  # " | " + content
        if total + needed <= max_width:
            used.append(col)
            total += needed

    overflow = [c for c in columns if c not in used]

    def _render_row(num: int | str, values: dict[str, str]) -> str:
        parts = [f"{num:>{row_num_width}}"]
        for col in used:
            cell = _truncate(str(values.get(col) if values.get(col) is not None else ""), col_widths[col])
            parts.append(f"{cell:<{col_widths[col]}}")
        return " | ".join(parts)

    header = _render_row("#", {col: col for col in used})
    separator = "-+-".join(
        ["-" * row_num_width] + ["-" * col_widths[col] for col in used]
    )

    lines = [header, separator]
    for i, row in enumerate(rows, start_row):
        lines.append(_render_row(i, row))  # type: ignore[arg-type]

    if overflow:
        lines.append(f"\n{len(overflow)} column(s) not shown: {', '.join(overflow)}")

    return "\n".join(lines)
