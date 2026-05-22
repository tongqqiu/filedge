import datetime
from typing import Any, Dict, List

from etl.config import ColumnMapping


class TransformError(Exception):
    pass


def _coerce_boolean(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise ValueError(f"cannot interpret {v!r} as boolean")


_COERCIONS = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": _coerce_boolean,
    "date": lambda v: datetime.date.fromisoformat(str(v)).isoformat(),
    "timestamp": lambda v: datetime.datetime.fromisoformat(str(v)).isoformat(),
}


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
            result[col.dest] = _COERCIONS[col.type](raw)
        except (ValueError, TypeError) as e:
            raise TransformError(
                f"Cannot coerce {col.source!r}={raw!r} to {col.type}: {e}"
            )
    return result
