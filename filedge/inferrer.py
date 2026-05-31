from dataclasses import dataclass, field
from itertools import islice
from typing import Iterator

from filedge.column_types import (
    ISO_DATE_FORMAT,
    boolean_set,
    date_format,
    date_like_note,
    has_time,
)


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


def infer_schema_from_parquet(schema) -> list[InferredColumn]:
    try:
        import pyarrow as pa
    except ImportError:
        raise ImportError("Parquet support requires pyarrow — run: uv sync --extra parquet")

    results = []
    for i in range(len(schema)):
        f = schema.field(i)
        t = f.type
        notes = ["schema read directly from Parquet file"]

        if pa.types.is_integer(t):
            filedge_type = "integer"
        elif pa.types.is_floating(t):
            filedge_type = "float"
        elif pa.types.is_boolean(t):
            filedge_type = "boolean"
        elif pa.types.is_date(t):
            filedge_type = "date"
        elif pa.types.is_timestamp(t):
            filedge_type = "timestamp"
        elif pa.types.is_string(t) or pa.types.is_large_string(t):
            filedge_type = "string"
        elif pa.types.is_struct(t):
            keys = sorted(t.field(j).name for j in range(t.num_fields))
            filedge_type = "string"
            notes.append(f"nested struct — keys: {', '.join(keys)}")
        elif pa.types.is_list(t) or pa.types.is_large_list(t):
            filedge_type = "string"
            notes.append("array value — cannot be ingested directly")
        else:
            filedge_type = "string"

        results.append(InferredColumn(f.name, filedge_type, "high", 0, 0, notes))

    return results


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
        elif boolean_set(non_null) is not None:
            results.append(InferredColumn(name, "boolean", confidence, null_count, total_seen))
        elif all(has_time(v) for v in non_null):
            results.append(InferredColumn(name, "timestamp", confidence, null_count, total_seen))
        else:
            formats = {date_format(v) for v in non_null}
            if formats == {ISO_DATE_FORMAT}:
                results.append(InferredColumn(name, "date", confidence, null_count, total_seen))
            elif None not in formats:
                # every value looks like a date, but in mixed / non-ISO formats —
                # genuinely conflicting evidence.
                results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen, [date_like_note(formats)]))
            else:
                numeric = sum(1 for v in non_null if _try_int(v) or _try_float(v))
                if numeric:
                    # Some values parse as numbers while others are text — conflicting
                    # type evidence (e.g. a numeric column with dirty rows). Worth review.
                    note = f"mixed values — {numeric} of {len(non_null)} look numeric"
                    results.append(InferredColumn(name, "string", "ambiguous", null_count, total_seen, [note]))
                else:
                    # Clean text: no value parses as another type, so `string` is a
                    # confident inference, not a fallback. Confidence tracks nulls.
                    results.append(InferredColumn(name, "string", confidence, null_count, total_seen))

    return results
