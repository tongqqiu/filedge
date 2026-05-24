from dataclasses import dataclass
from typing import Iterable

from filedge.config import CdcConfig


class CdcError(Exception):
    """Raised when CDC rows cannot be applied safely."""


@dataclass(frozen=True)
class CdcChange:
    operation: str
    key: tuple
    row: dict


def plan_cdc_changes(rows: Iterable[dict], cdc: CdcConfig) -> list[CdcChange]:
    operation_by_value = {
        value: operation
        for operation, values in cdc.operations.items()
        for value in values
    }
    latest_by_key = {}

    for row in rows:
        raw_operation = row.get(cdc.operation_column)
        operation = operation_by_value.get(raw_operation)
        if operation is None:
            raise CdcError(f"Unknown CDC operation: {raw_operation!r}")

        key = tuple(row.get(column) for column in cdc.keys)
        if any(value is None for value in key):
            raise CdcError("CDC key columns cannot be null")

        sequence = row.get(cdc.sequence_by)
        if sequence is None:
            raise CdcError(f"CDC sequence column {cdc.sequence_by!r} cannot be null")

        existing = latest_by_key.get(key)
        if existing is not None and existing[0] == sequence:
            raise CdcError(f"Multiple CDC rows for key {key!r} have the same sequence")
        if existing is None or sequence > existing[0]:
            latest_by_key[key] = (sequence, operation, dict(row))

    return [
        CdcChange(operation=operation, key=key, row=row)
        for key, (_sequence, operation, row) in latest_by_key.items()
    ]
