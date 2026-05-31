"""Re-drop a quarantine sidecar (ADR-0019) as a clean NDJSON File.

A quarantine sidecar holds one JSON object per line —
``{row_number, column, error, row}`` — with the original source row nested
under ``row`` and wrapped in diagnostic fields. That shape is **not**
re-droppable: dropping it back through ``filedge run`` would expose the
diagnostic keys at the top level and bury the real values one level down under
``row``, so nothing would parse into the declared columns.

This module unwraps each line back to its source ``row`` and writes the rows as
NDJSON — the canonical interchange format — so an operator can correct the bad
values and re-drop the File on the normal audited path under a new Content Hash
(ADR-0002). It is a pure transform: sidecar in, NDJSON out. It touches no Audit
DB and no Destination, and it does not mutate the input sidecar — producing a
re-droppable File never destroys the evidence it came from.
"""

import json
from typing import Any, Dict, Iterator, TextIO


class MalformedSidecarError(ValueError):
    """A quarantine sidecar line is not valid JSON or carries no ``row`` object."""


def read_quarantined_rows(sidecar: TextIO) -> Iterator[Dict[str, Any]]:
    """Yield each line's unwrapped source ``row``, dropping the diagnostic fields.

    Blank lines are skipped. A line that is not a JSON object, or whose ``row``
    is missing or not an object, raises ``MalformedSidecarError`` naming the line
    — the transform never silently drops data or emits a partial File.
    """
    for lineno, raw_line in enumerate(sidecar, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            raise MalformedSidecarError(
                f"sidecar line {lineno} is not valid JSON: {e}"
            ) from e
        if not isinstance(record, dict) or "row" not in record:
            raise MalformedSidecarError(
                f"sidecar line {lineno} has no 'row' field — not a quarantine sidecar line."
            )
        row = record["row"]
        if not isinstance(row, dict):
            raise MalformedSidecarError(
                f"sidecar line {lineno} 'row' is not a JSON object."
            )
        yield row


def redrop_quarantine(sidecar: TextIO, out: TextIO) -> int:
    """Read a quarantine sidecar, write its source rows as NDJSON, return the count.

    Each emitted line is the unwrapped source row with the diagnostic fields
    (``row_number``, ``column``, ``error``) stripped — ready to correct and
    re-drop through an NDJSON Pipeline.
    """
    count = 0
    for row in read_quarantined_rows(sidecar):
        out.write(json.dumps(row) + "\n")
        count += 1
    return count
