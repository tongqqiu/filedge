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

        if all(_try_int(v) for v in non_null):
            confidence = "high" if null_count == 0 else "low"
            results.append(InferredColumn(name, "integer", confidence, null_count, total_seen))
        elif all(_try_float(v) for v in non_null):
            confidence = "high" if null_count == 0 else "low"
            results.append(InferredColumn(name, "float", confidence, null_count, total_seen))
        else:
            results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen))

    return results
