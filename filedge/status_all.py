"""Fan a `status` read across every Pipeline in the Registry.

The single-Pipeline `status` path resolves one id, opens its one Audit DB, and
summarizes it. Operators with many Pipelines want the same read for all of them
at once. This module is that fan-out: it loads the Registry, resolves each entry,
and opens each Audit DB **independently** — one at a time, never joined — because
the one-Audit-DB-per-Pipeline rule (ADR-0004/ADR-0017) is structural, not an
optimization. Cross-Pipeline deduplication must stay impossible, so we never hold
two Audit DBs open together or query across them.

A single bad entry (an `audit_db` placeholder whose env var is unset, or a DB that
cannot be opened) must not sink the listing: that entry becomes an errored result
carrying its id and a message, iteration continues, and every healthy Pipeline
still reports. This module returns data only — no Click, no printing. The CLI owns
presentation.
"""

from dataclasses import dataclass
from typing import List, Optional

from filedge.audit_records import status_summary
from filedge.db import Database, create_audit_tables
from filedge.pipeline_registry import load_registry
from filedge.pipeline_resolution import resolve_pipeline


@dataclass(frozen=True)
class PipelineStatus:
    """One Pipeline's status in the fan-out.

    Exactly one of ``summary`` / ``error`` is set: ``summary`` is the
    ``status_summary`` dict when the Audit DB opened cleanly, otherwise ``error``
    carries a human-readable reason and ``summary`` is ``None``.
    """

    id: str
    summary: Optional[dict] = None
    error: Optional[str] = None


def status_all(workspace: str) -> List[PipelineStatus]:
    """Summarize every Pipeline in the Registry, in Registry order.

    Loads the Registry strictly (a malformed Registry raises, as it does for the
    Authoring side — never a partially-listed workspace). For each entry, resolves
    it and opens its Audit DB on its own, summarizes, then closes it before moving
    on. An entry that fails to resolve or whose Audit DB cannot be opened becomes
    an errored ``PipelineStatus`` and the fan-out continues.
    """
    registry = load_registry(workspace)
    results: List[PipelineStatus] = []
    for entry in registry.entries:
        results.append(_status_for(workspace, entry.id))
    return results


def _status_for(workspace: str, pipeline_id: str) -> PipelineStatus:
    db = None
    try:
        resolved = resolve_pipeline(workspace, pipeline_id)
        db = Database(resolved.audit_db_url)
        create_audit_tables(db)
        summary = status_summary(db)
        return PipelineStatus(id=pipeline_id, summary=summary)
    except Exception as e:  # noqa: BLE001 — one bad Audit DB must not sink the listing.
        return PipelineStatus(id=pipeline_id, error=str(e))
    finally:
        if db is not None:
            db.close()
