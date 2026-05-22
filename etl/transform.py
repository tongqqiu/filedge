from typing import Any, Dict, List

from etl.config import ColumnMapping


class TransformError(Exception):
    pass


def transform_row(row: Dict[str, Any], columns: List[ColumnMapping]) -> Dict[str, Any]:
    """Map source column names to destination names. Type coercion added in issue #2."""
    result = {}
    for col in columns:
        if col.source not in row:
            if col.required:
                raise TransformError(f"Missing required column: {col.source!r}")
            result[col.dest] = None
            continue
        result[col.dest] = row[col.source]
    return result
