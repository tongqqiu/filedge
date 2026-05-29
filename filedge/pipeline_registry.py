"""The Pipeline Registry — the passive YAML index of authored Pipelines
(CONTEXT.md: Pipeline Registry; ADR-0017).

A single `pipeline-registry.yaml` at the workspace root lists every Pipeline and
references the four things the one-Audit-DB-per-Pipeline rule names: the Pipeline
Folder, the Watched Directory, an Audit DB connection placeholder, and the Audit
Export destination. This module reads, validates, and rewrites that file. It is a
passive index — never a daemon, lock manager, or query engine.

The reader rejects a malformed Registry rather than tolerate it: a missing field,
a duplicate `id`, a `folder` that does not exist or lacks `pipeline.yaml`, a
literal (non-placeholder) `audit_db`, or — the load-bearing audit check — two
entries sharing one `audit_db` placeholder, which would point two Pipelines at one
Audit DB and reintroduce cross-Pipeline deduplication.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml

REGISTRY_FILENAME = "pipeline-registry.yaml"
REGISTRY_VERSION = 1
_REQUIRED_FIELDS = ("id", "folder", "watched_directory", "audit_db", "audit_export")


class RegistryError(ValueError):
    """Raised when a Pipeline Registry is malformed and must not be loaded."""


@dataclass
class RegistryEntry:
    """One Pipeline's entry in the Registry — four references, no inline config."""

    id: str
    folder: str
    watched_directory: str
    audit_db: str  # Audit DB connection placeholder (env:NAME / secrets:/path)
    audit_export: str

    def to_dict(self) -> dict:
        # Explicit field order keeps the on-disk YAML stable and review-friendly.
        return {
            "id": self.id,
            "folder": self.folder,
            "watched_directory": self.watched_directory,
            "audit_db": self.audit_db,
            "audit_export": self.audit_export,
        }


@dataclass
class PipelineRegistry:
    """The whole Registry: a schema version and an ordered list of entries."""

    version: int = REGISTRY_VERSION
    entries: List[RegistryEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "pipelines": [e.to_dict() for e in self.entries],
        }


def registry_path(workspace: str) -> str:
    """The conventional Registry location: `<workspace>/pipeline-registry.yaml`."""
    return os.path.join(workspace, REGISTRY_FILENAME)


def registry_exists(workspace: str) -> bool:
    return os.path.isfile(registry_path(workspace))


def _is_placeholder(value: str) -> bool:
    """True for an `env:NAME` or `secrets:/abs/path` reference (never a literal)."""
    if value.startswith("env:") and value[len("env:") :]:
        return True
    if value.startswith("secrets:/") and value[len("secrets:") :]:
        return True
    return False


def parse_registry(data: dict, *, workspace: Optional[str] = None) -> PipelineRegistry:
    """Validate a parsed Registry mapping, rejecting any malformed entry.

    When `workspace` is given, each entry's `folder` is resolved against it and
    checked to exist and contain `pipeline.yaml`; pass `None` to validate the
    schema alone (no filesystem).
    """
    if not isinstance(data, dict):
        raise RegistryError("Pipeline Registry must be a mapping.")
    version = data.get("version")
    if version != REGISTRY_VERSION:
        raise RegistryError(
            f"Unsupported Pipeline Registry version {version!r}; "
            f"expected {REGISTRY_VERSION}."
        )
    raw_entries = data.get("pipelines")
    if not isinstance(raw_entries, list):
        raise RegistryError("Pipeline Registry must have a 'pipelines:' list.")

    entries: List[RegistryEntry] = []
    seen_ids: set = set()
    audit_db_owner: dict = {}

    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise RegistryError("Each Pipeline Registry entry must be a mapping.")
        for required in _REQUIRED_FIELDS:
            if not raw.get(required):
                raise RegistryError(
                    f"Pipeline Registry entry missing required field {required!r}."
                )
        entry = RegistryEntry(
            id=raw["id"],
            folder=raw["folder"],
            watched_directory=raw["watched_directory"],
            audit_db=raw["audit_db"],
            audit_export=raw["audit_export"],
        )

        if entry.id in seen_ids:
            raise RegistryError(f"Duplicate Pipeline id {entry.id!r} in the Registry.")
        seen_ids.add(entry.id)

        if not _is_placeholder(entry.audit_db):
            raise RegistryError(
                f"audit_db for Pipeline {entry.id!r} must be an env:/secrets: "
                "placeholder, not a literal connection string — no secret may be "
                "written to an authored artifact."
            )
        if entry.audit_db in audit_db_owner:
            raise RegistryError(
                f"Audit DB placeholder {entry.audit_db!r} is shared by Pipelines "
                f"{audit_db_owner[entry.audit_db]!r} and {entry.id!r}; one Audit DB "
                "maps to exactly one Pipeline."
            )
        audit_db_owner[entry.audit_db] = entry.id

        if workspace is not None:
            folder_abs = os.path.join(workspace, entry.folder)
            if not os.path.isdir(folder_abs):
                raise RegistryError(
                    f"Pipeline Folder {entry.folder!r} for {entry.id!r} does not exist."
                )
            if not os.path.isfile(os.path.join(folder_abs, "pipeline.yaml")):
                raise RegistryError(
                    f"Pipeline Folder {entry.folder!r} for {entry.id!r} lacks "
                    "pipeline.yaml."
                )

        entries.append(entry)

    return PipelineRegistry(version=version, entries=entries)


def load_registry(workspace: str) -> PipelineRegistry:
    """Read and validate the Registry at the workspace root."""
    path = registry_path(workspace)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No Pipeline Registry at {path!r}.")
    with open(path) as f:
        data = yaml.safe_load(f)
    return parse_registry(data, workspace=workspace)


def add_entry(workspace: str, entry: RegistryEntry) -> PipelineRegistry:
    """Append one entry, creating the Registry lazily on the first Pipeline.

    Reads the existing Registry (or starts an empty one), appends the new entry,
    re-validates the combined Registry — so a duplicate `id` or a reused
    `audit_db` is rejected before anything is written — and rewrites the file.
    Existing entries are preserved verbatim; growth is additive and order-stable.
    Audit DBs are never merged.
    """
    registry = load_registry(workspace) if registry_exists(workspace) else PipelineRegistry()
    registry.entries.append(entry)
    validated = parse_registry(registry.to_dict(), workspace=workspace)
    _write_registry(workspace, validated)
    return validated


def _write_registry(workspace: str, registry: PipelineRegistry) -> None:
    with open(registry_path(workspace), "w") as f:
        yaml.safe_dump(registry.to_dict(), f, sort_keys=False)
