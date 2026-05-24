import datetime
import re
from typing import Any

FILEDGE_TYPES = ("string", "integer", "float", "date", "timestamp", "boolean")
ISO_DATE_FORMAT = "YYYY-MM-DD"

_BOOLEAN_SETS = [
    {"true", "false"},
    {"yes", "no"},
]

_DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), ISO_DATE_FORMAT),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "MM/DD/YYYY"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "DD-MM-YYYY"),
]

_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def validate_column_type(column_type: str) -> None:
    if column_type not in FILEDGE_TYPES:
        raise ValueError(
            f"Unknown Filedge column type {column_type!r}. "
            f"Supported types: {', '.join(FILEDGE_TYPES)}"
        )


def coerce_value(column_type: str, value: Any) -> Any:
    validate_column_type(column_type)
    if column_type == "string":
        return str(value)
    if column_type == "integer":
        return int(value)
    if column_type == "float":
        return float(value)
    if column_type == "boolean":
        return coerce_boolean(value)
    if column_type == "date":
        return datetime.date.fromisoformat(str(value)).isoformat()
    if column_type == "timestamp":
        return datetime.datetime.fromisoformat(str(value)).isoformat()
    raise AssertionError(f"Unhandled Filedge column type: {column_type}")


def coerce_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise ValueError(f"cannot interpret {value!r} as boolean")


def boolean_set(values: list[str]):
    lower = {value.lower() for value in values}
    for boolean_values in _BOOLEAN_SETS:
        if lower <= boolean_values:
            return boolean_values
    return None


def date_format(value: str):
    for pattern, label in _DATE_PATTERNS:
        if pattern.match(value):
            return label
    return None


def has_time(value: str) -> bool:
    return bool(_DATETIME_RE.match(value))


def date_like_note(formats: set[str]) -> str:
    labels = ", ".join(sorted(formats))
    return f"date-like {labels}; filedge date requires {ISO_DATE_FORMAT}"
