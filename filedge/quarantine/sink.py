"""Buffer bad rows during a File load and flush them to an NDJSON sidecar.

The Quarantine sink collects rows that fail Transform/Field Encryption — with
their row number, offending column, error, and the raw source row — and writes
them as a single NDJSON File in the configured quarantine location, but **only**
when ``finalize()`` is called. Nothing is written until then, so a File that
ends up failing wholesale (over the quarantine threshold) leaves no sidecar
behind: a sidecar's presence always means a real, finalized quarantine.

Buffering in memory (rather than streaming straight to disk) is deliberate — the
quarantine threshold bounds how many bad rows can accumulate before the File
fails instead, so the buffer stays small. The sidecar filename ties back to the
source File and its Content Hash so operators can correlate it with the Audit
Record.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

SIDECAR_SUFFIX = ".quarantine.ndjson"


@dataclass
class _QuarantinedRow:
    row_number: int
    column: str
    error: str
    row: Dict[str, Any]


class QuarantineSink:
    """Collect bad rows for one File and write them to an NDJSON sidecar on finalize."""

    def __init__(self, quarantine_dir: str, source_filename: str, content_hash: str):
        self._dir = quarantine_dir
        self._source_filename = source_filename
        self._content_hash = content_hash
        self._buffer: List[_QuarantinedRow] = []

    @property
    def count(self) -> int:
        """How many bad rows have been recorded so far."""
        return len(self._buffer)

    def record(self, row_number: int, column: str, error: str, row: Dict[str, Any]) -> None:
        """Buffer one bad row (not written to disk until finalize)."""
        self._buffer.append(_QuarantinedRow(row_number, column, error, dict(row)))

    def sidecar_name(self) -> str:
        """`<source-stem>.<short-hash>.quarantine.ndjson` — tied to File + hash."""
        stem = os.path.splitext(os.path.basename(self._source_filename))[0]
        return f"{stem}.{self._content_hash[:12]}{SIDECAR_SUFFIX}"

    def finalize(self) -> str:
        """Write the buffered bad rows as an NDJSON sidecar; return its path.

        Call only when the File is finalized (under threshold). Each line is a
        JSON object: ``{row_number, column, error, row}``.
        """
        os.makedirs(self._dir, exist_ok=True)
        path = os.path.join(self._dir, self.sidecar_name())
        with open(path, "w", encoding="utf-8") as f:
            for q in self._buffer:
                f.write(json.dumps({
                    "row_number": q.row_number,
                    "column": q.column,
                    "error": q.error,
                    "row": q.row,
                }) + "\n")
        return path

    def discard(self) -> None:
        """Drop the buffer without writing anything (the File failed wholesale)."""
        self._buffer.clear()
