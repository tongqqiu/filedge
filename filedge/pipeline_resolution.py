"""Resolve a Pipeline Registry id into the values an operator command needs.

The operator commands (`run`, `status`, `requeue`, `lineage`, `export-audit`)
have always taken an explicit `--config` / `--audit-db-url` / `--dir` triple. The
Pipeline Registry already records those references per Pipeline; this module is
the single seam that turns a Pipeline `id` into the concrete values, so each
command goes from "a Pipeline id" to "the flags it already accepts" without
re-implementing the lookup.

Resolution is strict: it loads the Registry through the Registry's own
`load_registry` (so a duplicate id, a shared `audit_db`, or a missing Folder
fails the same way it does for the Authoring side — an invalid workspace is never
partially operated on), looks the entry up by id, and resolves the `audit_db`
placeholder to a real connection string only here, at command time. An unknown id
raises `PipelineNotFound` naming the id and listing the known ones.
"""

import os
from dataclasses import dataclass
from typing import List

from filedge.pipeline_folder import CONFIG_FILENAME
from filedge.pipeline_registry import load_registry
from filedge.reference import resolve_reference

_AUDIT_DB_USAGE = "audit_db"


class PipelineNotFound(ValueError):
    """Raised when a Pipeline id is not present in the Registry."""


@dataclass(frozen=True)
class ResolvedPipeline:
    """The four concrete values an operator command needs for one Pipeline.

    ``audit_db_url`` is the resolved connection string, not the Registry
    placeholder — it is produced only at resolution time and never persisted.
    """

    id: str
    config_path: str
    watched_directory: str
    audit_db_url: str
    audit_export: str


def resolve_pipeline(workspace: str, pipeline_id: str) -> ResolvedPipeline:
    """Resolve one Pipeline id against the Registry at ``workspace``."""
    registry = load_registry(workspace)
    for entry in registry.entries:
        if entry.id == pipeline_id:
            folder_abs = os.path.join(workspace, entry.folder)
            return ResolvedPipeline(
                id=entry.id,
                config_path=os.path.join(folder_abs, CONFIG_FILENAME),
                watched_directory=entry.watched_directory,
                audit_db_url=resolve_reference(entry.audit_db, usage=_AUDIT_DB_USAGE),
                audit_export=entry.audit_export,
            )
    raise PipelineNotFound(
        f"No Pipeline {pipeline_id!r} in the Registry. Known: "
        f"{_known_ids(registry.entries)}."
    )


def _known_ids(entries) -> str:
    ids: List[str] = [e.id for e in entries]
    return ", ".join(repr(i) for i in ids) if ids else "(none)"
