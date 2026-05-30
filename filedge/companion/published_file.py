"""Publish a complete File with its Source Manifest.

This module owns the reliability sequence shared by external companion jobs:
write a complete staged NDJSON File, emit its Source Manifest sidecar, hold the
Fetch Lock, promote the sidecar first, then promote the data File last.
Post-promotion state updates stay with the caller.
"""

import uuid
from dataclasses import dataclass
from typing import List, Optional

from filedge.companion.manifest import emit_manifest
from filedge.companion.promotion import FetchLock, promote
from filedge.companion.staging import write_staged_ndjson


@dataclass(frozen=True)
class PublishRequest:
    records: List[dict]
    staging_dir: str
    watched_directory: str
    state_dir: str
    source_name: str
    source_type: str
    producer: str
    started_at: str
    finished_at: str
    from_cursor: Optional[str]
    to_cursor: Optional[str]
    source_range: Optional[dict]
    gzip: bool = False


@dataclass(frozen=True)
class PublishedFile:
    data_path: str
    sidecar_path: str


def publish_file(request: PublishRequest) -> PublishedFile:
    """Stage, manifest, and promote one complete File under a Fetch Lock."""
    data_path = write_staged_ndjson(
        request.records,
        request.staging_dir,
        request.source_name,
        from_cursor=request.from_cursor,
        to_cursor=request.to_cursor,
        timestamp=request.finished_at,
        gzip_enabled=request.gzip,
    )
    sidecar_path = emit_manifest(
        data_path,
        source_type=request.source_type,
        source_name=request.source_name,
        producer=request.producer,
        run_id=uuid.uuid4().hex,
        started_at=request.started_at,
        finished_at=request.finished_at,
        record_count=len(request.records),
        source_range=request.source_range,
    )

    with FetchLock(request.state_dir, request.source_name):
        promotion = promote(data_path, sidecar_path, request.watched_directory)
    return PublishedFile(
        data_path=promotion.data_path,
        sidecar_path=promotion.sidecar_path,
    )
