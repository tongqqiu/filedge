from typing import Any, Dict, List

from filedge.column_types import coerce_value
from filedge.config import ColumnMapping


class TransformError(Exception):
    pass


def transform_row(row: Dict[str, Any], columns: List[ColumnMapping]) -> Dict[str, Any]:
    result = {}
    for col in columns:
        if col.source not in row:
            if col.required:
                raise TransformError(f"Missing required column: {col.source!r}")
            result[col.dest] = None
            continue
        raw = row[col.source]
        if raw is None or raw == "":
            if col.required:
                raise TransformError(f"Required column {col.source!r} is empty")
            result[col.dest] = None
            continue
        try:
            result[col.dest] = coerce_value(col.type, raw)
        except (ValueError, TypeError) as e:
            raise TransformError(
                f"Cannot coerce {col.source!r}={raw!r} to {col.type}: {e}"
            )
    return result
