"""Fixed-width Parser primitives — slicer, layout validator, and parser shim.

The slicer and validator are pure functions intentionally kept free of any
filesystem or YAML coupling so they can be tested exhaustively in isolation.
See ADR-0013 for the architectural decisions captured here.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterator, List

from filedge.parser import Parser

if TYPE_CHECKING:
    from filedge.config import ColumnMapping


@dataclass(frozen=True)
class LayoutColumn:
    """One declared column in a fixed-width layout.

    `start` is 1-indexed (matching partner record-layout specs).
    `width` is the number of bytes the column occupies.
    """

    name: str
    start: int
    width: int


def layout_from_columns(columns: "List[ColumnMapping]") -> List[LayoutColumn]:
    """Translate Pipeline Config columns into a slicer-ready Fixed-Width Layout.

    The single source of truth for turning a column's `start`/`width` into a
    `LayoutColumn`. Shared by config-load validation, the runtime loader, and
    the Authoring Session so the three never drift.
    """
    return [LayoutColumn(name=c.source, start=c.start, width=c.width) for c in columns]


class FixedWidthLayoutError(ValueError):
    """Raised when a declared fixed-width layout is internally invalid."""


class FixedWidthLineTooShortError(ValueError):
    """Raised at runtime when a data line is shorter than the declared layout."""


def slice_line(line: str, layout: List[LayoutColumn]) -> Dict[str, str]:
    """Apply a sorted, validated layout to one line.

    Returns a dict of column name → stripped value. Trailing bytes past the
    last declared column are silently ignored. Callers are responsible for
    blank-line skipping and short-line detection.
    """
    return {column.name: line[column.start - 1 : column.start - 1 + column.width].strip()
            for column in layout}


def validate_layout(layout: List[LayoutColumn]) -> None:
    """Reject malformed layouts at YAML load time.

    The error messages name the offending columns and byte positions so the
    operator can fix the layout without a hex-dump exercise.
    """
    for column in layout:
        if column.width <= 0:
            raise FixedWidthLayoutError(
                f"Column {column.name!r} has width={column.width}; width must be > 0."
            )
        if column.start < 1:
            raise FixedWidthLayoutError(
                f"Column {column.name!r} has start={column.start}; start must be >= 1 (1-indexed)."
            )

    for previous, current in zip(layout, layout[1:]):
        if current.start < previous.start:
            raise FixedWidthLayoutError(
                f"Columns must be declared sorted by start; "
                f"{current.name!r} (start={current.start}) appears after "
                f"{previous.name!r} (start={previous.start})."
            )

    for i, a in enumerate(layout):
        a_end = a.start + a.width - 1
        for b in layout[i + 1 :]:
            b_end = b.start + b.width - 1
            if a.start <= b_end and b.start <= a_end:
                overlap_start = max(a.start, b.start)
                overlap_end = min(a_end, b_end)
                raise FixedWidthLayoutError(
                    f"Columns {a.name!r} (start={a.start}, width={a.width}) and "
                    f"{b.name!r} (start={b.start}, width={b.width}) overlap at byte "
                    f"positions {overlap_start}-{overlap_end}. Each byte must belong to "
                    f"at most one column."
                )


def _required_line_length(layout: List[LayoutColumn]) -> int:
    return max((column.start + column.width - 1 for column in layout), default=0)


class FixedWidthParser(Parser):
    """Iterate a fixed-width text file as dict rows.

    The layout must already have been validated via `validate_layout` (the
    config loader does this at YAML load time).
    """

    def __init__(self, layout: List[LayoutColumn]):
        self._layout = layout
        self._required_length = _required_line_length(layout)

    def parse(self, fileobj) -> Iterator[Dict[str, Any]]:
        for line in fileobj:
            stripped_newline = line.rstrip("\r\n")
            if not stripped_newline.strip():
                continue
            if len(stripped_newline) < self._required_length:
                last_column = max(self._layout, key=lambda c: c.start + c.width)
                raise FixedWidthLineTooShortError(
                    f"line is {len(stripped_newline)} bytes long but the declared "
                    f"layout requires at least {self._required_length} bytes "
                    f"(last column {last_column.name!r} ends at byte {self._required_length})."
                )
            yield slice_line(stripped_newline, self._layout)
