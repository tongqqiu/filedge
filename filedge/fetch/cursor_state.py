"""The incremental Cursor State store.

The Fetcher pulls only records newer than the last successful run. That "last
successful" marker is the cursor, persisted in the Fetcher's own state area
(CONTEXT.md: Sources Config names a state path) keyed by API Source. The
load-bearing rule (ADR-0006: partial fetches must not be visible): the cursor is
advanced *only after* a File is successfully promoted, so a crash between fetch
and promotion retries the same window rather than skipping data.

This is plain local state — not an Audit DB record and not part of the
`filedge run` state machine.
"""

import json
import os
from typing import Optional

_SUFFIX = ".cursor.json"


class CursorStore:
    """Read and advance the per-source incremental cursor under `state_dir`."""

    def __init__(self, state_dir: str):
        self._state_dir = state_dir

    def _path(self, source_name: str) -> str:
        return os.path.join(self._state_dir, source_name + _SUFFIX)

    def read(self, source_name: str) -> Optional[str]:
        """Return the last advanced cursor, or None on a first run."""
        try:
            with open(self._path(source_name)) as f:
                return json.load(f).get("cursor")
        except FileNotFoundError:
            return None

    def advance(self, source_name: str, cursor: str, *, updated_at: Optional[str] = None) -> None:
        """Persist `cursor` as the new high-water mark for `source_name`.

        Call this only after a successful promotion. Writing is atomic (write a
        temp file, then replace) so a crash mid-write cannot corrupt the cursor.
        """
        os.makedirs(self._state_dir, exist_ok=True)
        path = self._path(source_name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"cursor": cursor, "updated_at": updated_at}, f)
        os.replace(tmp, path)
