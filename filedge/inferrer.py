import re
from dataclasses import dataclass, field
from itertools import islice
from typing import Iterator


@dataclass
class InferredColumn:
    name: str
    inferred_type: str
    confidence: str
    null_count: int
    total_seen: int
    notes: list = field(default_factory=list)


def _is_null(v) -> bool:
    return v is None or v == ""


def _try_int(v: str) -> bool:
    try:
        int(v)
        return True
    except (ValueError, TypeError):
        return False


def _try_float(v: str) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


_BOOLEAN_SETS = [
    {"true", "false"},
    {"yes", "no"},
]

_DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "MM/DD/YYYY"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "DD-MM-YYYY"),
]

_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
)


def _boolean_set(values: list[str]):
    """Return the matching boolean set if all values belong to one, else None."""
    lower = {v.lower() for v in values}
    for bset in _BOOLEAN_SETS:
        if lower <= bset:
            return bset
    return None


def _date_format(v: str):
    for pattern, label in _DATE_PATTERNS:
        if pattern.match(v):
            return label
    return None


def _has_time(v: str) -> bool:
    return bool(_DATETIME_RE.match(v))


def infer_schema(rows: Iterator[dict], sample_rows: int = 1000) -> list[InferredColumn]:
    col_values: dict[str, list] = {}
    total_seen = 0

    for row in islice(rows, sample_rows):
        total_seen += 1
        for k, v in row.items():
            col_values.setdefault(k, []).append(v)

    results = []
    for name, values in col_values.items():
        null_count = sum(1 for v in values if _is_null(v))
        non_null = [v for v in values if not _is_null(v)]

        if not non_null:
            results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen))
            continue

        # Detect non-scalar values (dict / list) before any string-based checks
        dicts = [v for v in non_null if isinstance(v, dict)]
        arrays = [v for v in non_null if isinstance(v, list)]
        if dicts:
            keys = sorted({k for d in dicts for k in d})
            note = f"nested object — keys: {', '.join(keys)}"
            results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen, [note]))
            continue
        if arrays:
            results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen, ["array value — cannot be ingested directly"]))
            continue
        if any(not isinstance(v, str) for v in non_null):
            results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen))
            continue

        confidence = "high" if null_count == 0 else "low"

        if all(_try_int(v) for v in non_null):
            results.append(InferredColumn(name, "integer", confidence, null_count, total_seen))
        elif all(_try_float(v) for v in non_null):
            results.append(InferredColumn(name, "float", confidence, null_count, total_seen))
        elif _boolean_set(non_null) is not None:
            results.append(InferredColumn(name, "boolean", confidence, null_count, total_seen))
        elif all(_has_time(v) for v in non_null):
            results.append(InferredColumn(name, "timestamp", confidence, null_count, total_seen))
        else:
            formats = {_date_format(v) for v in non_null}
            if None not in formats and len(formats) == 1:
                results.append(InferredColumn(name, "date", confidence, null_count, total_seen))
            elif None not in formats and len(formats) > 1:
                note = f"multiple date formats detected: {', '.join(sorted(formats))}"
                results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen, [note]))
            else:
                results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen))

    return results
