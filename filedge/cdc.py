from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from filedge.config import CdcConfig


class CdcError(Exception):
    """Raised when CDC rows cannot be applied safely."""


@dataclass(frozen=True)
class CdcChange:
    operation: str
    key: tuple
    row: dict


PROVENANCE_COLUMNS = ("_source_file_hash", "_ingested_at")


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


class TransactionalCdcAdapter(Protocol):
    """Dialect operations a transactional Connector exposes to apply CDC changes.

    The orchestrator owns the change-by-change delete-then-insert sequence and the
    provenance columns; the adapter owns identifier quoting, parameter placeholders,
    and statement execution. Transaction boundaries are owned by the Connector
    outside the orchestrator.
    """

    def delete_by_key(
        self,
        table: str,
        key_columns: Sequence[str],
        key_values: Sequence,
    ) -> None: ...

    def insert_row(
        self,
        table: str,
        columns: Sequence[str],
        values: Sequence,
    ) -> None: ...


def apply_transactional_cdc(
    adapter: TransactionalCdcAdapter,
    table: str,
    rows: Iterable[dict],
    file_hash: str,
    ingested_at: str,
    cdc: CdcConfig,
) -> None:
    """Plan CDC changes from rows and apply them via the adapter.

    For each planned change, the orchestrator deletes by key and then (for non-delete
    operations) inserts the row's data columns plus `_source_file_hash` and
    `_ingested_at`. The caller is responsible for the surrounding transaction.
    """
    changes = plan_cdc_changes(rows, cdc)
    for change in changes:
        adapter.delete_by_key(table, cdc.keys, list(change.key))
        if change.operation == "delete":
            continue
        row = {
            key: value
            for key, value in change.row.items()
            if key != cdc.operation_column
        }
        columns = list(row.keys()) + list(PROVENANCE_COLUMNS)
        values = list(row.values()) + [file_hash, ingested_at]
        adapter.insert_row(table, columns, values)
