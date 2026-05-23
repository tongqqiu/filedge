from dataclasses import dataclass
from typing import Iterator, List

from etl.config import ColumnMapping
from etl.transform import TransformError, transform_row


@dataclass
class RowFailure:
    row_number: int
    column: str
    error: str


@dataclass
class ValidationResult:
    rows_checked: int
    failures: List[RowFailure]
    undeclared_columns: List[str]


def validate_file(
    rows: Iterator[dict],
    columns: List[ColumnMapping],
) -> ValidationResult:
    failures: List[RowFailure] = []
    undeclared: List[str] = []
    declared = {col.source for col in columns}
    rows_checked = 0
    first = True

    for row in rows:
        if first:
            undeclared = [k for k in row if k not in declared]
            first = False
        rows_checked += 1
        for col in columns:
            try:
                transform_row(row, [col])
            except TransformError as e:
                failures.append(RowFailure(
                    row_number=rows_checked,
                    column=col.source,
                    error=str(e),
                ))

    return ValidationResult(
        rows_checked=rows_checked,
        failures=failures,
        undeclared_columns=undeclared,
    )
