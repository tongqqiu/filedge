"""Wire the Reference Fetcher together for one API Source.

The ordering is the whole point and is fixed here:

    load plan -> read cursor -> fetch pages -> (no records? no-op) ->
    write complete staged NDJSON -> emit Source Manifest sidecar ->
    [Fetch Lock] promote sidecar+data into Watched Directory -> advance cursor

The cursor advances **only after** a successful promotion, so a crash anywhere
earlier retries the same window rather than skipping data (ADR-0006). A run that
fetches zero new records is a clean no-op: nothing is staged, promoted, or
advanced.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from filedge.companion.manifest import emit_manifest
from filedge.companion.promotion import FetchLock, promote
from filedge.companion.staging import staged_filename, write_staged_ndjson
from filedge.fetch.cursor_state import CursorStore
from filedge.fetch.source_client import HttpSourceClient
from filedge.fetch.sources_config import FetchPlan, load_sources


@dataclass(frozen=True)
class FetchOutcome:
    source_name: str
    record_count: int
    from_cursor: Optional[str]
    to_cursor: Optional[str]
    dry_run: bool = False
    skipped: bool = False
    data_path: Optional[str] = None
    sidecar_path: Optional[str] = None
    target_filename: Optional[str] = None


def run_fetch(
    config_path: str,
    source_name: str,
    *,
    dry_run: bool = False,
    client: Optional[HttpSourceClient] = None,
) -> FetchOutcome:
    """Pull one API Source per the Sources Config and promote a File (or no-op)."""
    plan = load_sources(config_path, source_name)
    cursor_store = CursorStore(plan.state_dir)
    from_cursor = cursor_store.read(source_name)
    client = client or HttpSourceClient()

    if dry_run:
        return _dry_run_outcome(plan, from_cursor)

    result = client.fetch(plan, from_cursor)
    if not result.records:
        return FetchOutcome(
            source_name=source_name,
            record_count=0,
            from_cursor=from_cursor,
            to_cursor=from_cursor,
            skipped=True,
        )

    to_cursor = result.next_cursor
    data_path = write_staged_ndjson(
        result.records,
        plan.staging_dir,
        source_name,
        from_cursor=from_cursor,
        to_cursor=to_cursor,
        timestamp=result.finished_at,
        gzip_enabled=plan.gzip,
    )
    sidecar_path = emit_manifest(
        data_path,
        source_type=plan.source_type,
        source_name=source_name,
        producer=plan.producer,
        run_id=uuid.uuid4().hex,
        started_at=result.started_at,
        finished_at=result.finished_at,
        record_count=len(result.records),
        source_range={
            "cursor_param": plan.cursor_param,
            "from": from_cursor,
            "to": to_cursor,
        },
    )

    with FetchLock(plan.state_dir, source_name):
        promotion = promote(data_path, sidecar_path, plan.watched_directory)

    # Cursor advances only now — after the File is durably in the Watched Directory.
    if to_cursor is not None:
        cursor_store.advance(source_name, to_cursor, updated_at=result.finished_at)

    return FetchOutcome(
        source_name=source_name,
        record_count=len(result.records),
        from_cursor=from_cursor,
        to_cursor=to_cursor,
        data_path=promotion.data_path,
        sidecar_path=promotion.sidecar_path,
    )


def _dry_run_outcome(plan: FetchPlan, from_cursor: Optional[str]) -> FetchOutcome:
    target = staged_filename(
        plan.source_name, from_cursor, None, "DRYRUN", gzip_enabled=plan.gzip
    )
    return FetchOutcome(
        source_name=plan.source_name,
        record_count=0,
        from_cursor=from_cursor,
        to_cursor=None,
        dry_run=True,
        target_filename=target,
    )
