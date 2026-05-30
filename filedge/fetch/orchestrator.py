"""Wire the Reference Fetcher together for one API Source.

The ordering is the whole point and is fixed here:

    load plan -> read cursor -> fetch source records -> (no records? no-op) ->
    publish complete File + Source Manifest under Fetch Lock -> advance cursor

The cursor advances **only after** a successful promotion, so a crash anywhere
earlier retries the same window rather than skipping data (ADR-0006). A run that
fetches zero new records is a clean no-op: nothing is staged, promoted, or
advanced.
"""

from dataclasses import dataclass
from typing import Optional

from filedge.companion.published_file import PublishRequest, publish_file
from filedge.companion.staging import staged_filename
from filedge.fetch.cursor_state import CursorStore
from filedge.fetch.source_client import HttpSourceClient
from filedge.fetch.sources_config import load_sources


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
    published = publish_file(
        PublishRequest(
            records=result.records,
            staging_dir=plan.staging_dir,
            watched_directory=plan.watched_directory,
            state_dir=plan.state_dir,
            source_name=source_name,
            source_type=plan.source_type,
            producer=plan.producer,
            started_at=result.started_at,
            finished_at=result.finished_at,
            from_cursor=from_cursor,
            to_cursor=to_cursor,
            source_range=plan.source.source_range(from_cursor, to_cursor),
            gzip=plan.gzip,
        )
    )

    # Cursor advances only now — after the File is durably in the Watched Directory.
    if to_cursor is not None:
        cursor_store.advance(source_name, to_cursor, updated_at=result.finished_at)

    return FetchOutcome(
        source_name=source_name,
        record_count=len(result.records),
        from_cursor=from_cursor,
        to_cursor=to_cursor,
        data_path=published.data_path,
        sidecar_path=published.sidecar_path,
    )


def _dry_run_outcome(plan, from_cursor: Optional[str]) -> FetchOutcome:
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
