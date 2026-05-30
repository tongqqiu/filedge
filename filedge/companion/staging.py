"""Write a fetched batch as one complete NDJSON File into a staging area.

NDJSON is Filedge's canonical format. This module materializes a window's
records as a single complete File in the *staging* area — never directly in the
Watched Directory, so a half-written or failed fetch is never visible to
`filedge run` (ADR-0006). Promotion into the Watched Directory is a separate,
locked step. Optional gzip mirrors Compaction's output economics.

The filename encodes the source and cursor window so an Operator can trace a
File back to its fetch run by name alone.
"""

import gzip
import json
import os
import re
from typing import List, Optional

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: Optional[str]) -> str:
    if not value:
        return "none"
    return _UNSAFE.sub("-", value).strip("-") or "none"


def staged_filename(source_name: str, from_cursor: Optional[str], to_cursor: Optional[str],
                    timestamp: str, *, gzip_enabled: bool) -> str:
    """`<source>-<from>-<to>-<ts>.ndjson[.gz]` — a window-tagged, unique name."""
    name = f"{_slug(source_name)}-{_slug(from_cursor)}-{_slug(to_cursor)}-{_slug(timestamp)}.ndjson"
    return name + ".gz" if gzip_enabled else name


def write_staged_ndjson(
    records: List[dict],
    staging_dir: str,
    source_name: str,
    *,
    from_cursor: Optional[str],
    to_cursor: Optional[str],
    timestamp: str,
    gzip_enabled: bool = False,
) -> str:
    """Write `records` as one complete NDJSON File in `staging_dir`; return its path.

    The File is written in full before this returns: the Fetcher only ever
    promotes complete Files, and a complete File is what lands here first.
    """
    os.makedirs(staging_dir, exist_ok=True)
    filename = staged_filename(
        source_name, from_cursor, to_cursor, timestamp, gzip_enabled=gzip_enabled
    )
    path = os.path.join(staging_dir, filename)
    payload = "".join(json.dumps(record) + "\n" for record in records)

    if gzip_enabled:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(payload)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
    return path
