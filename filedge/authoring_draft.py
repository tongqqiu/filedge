"""The Pipeline Config Draft — the editable core of the Authoring Workflow.

`PipelineConfigDraft` is the headless data model the Authoring UI (ADR-0016)
will sit on top of: build it from a sample File, run Schema Inference once,
review and edit the per-column source name, destination name, Column Type, and
required flag, then emit a Pipeline Config that round-trips through the same
config loading the Operator CLI uses. It reuses `AuthoringSession` for Schema
Inference and `config_from_dict` for the round-trip; it reimplements no domain
rule, holds no secrets, and writes nothing to disk (ADR-0015).

This slice is deliberately narrow (#146): CSV only, `write_mode: append`, and a
placeholder Connector block sufficient for config loading. Field Encryption, CDC
settings, and the Connector picker arrive in later Authoring UI slices.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from filedge.authoring import AuthoringSession
from filedge.column_types import validate_column_type
from filedge.config import PipelineConfig, config_from_dict

# A placeholder Destination Connector, enough for config loading to pass. The
# Connector picker (#152) replaces it with a real type and non-secret settings.
_PLACEHOLDER_CONNECTOR = {"type": "sqlite", "url": "sqlite:///REPLACE_ME.db"}


@dataclass
class ColumnDraft:
    """One editable column in a Pipeline Config Draft.

    `source`, `dest`, `type`, and `required` are the authored fields. The
    remaining fields carry Schema Inference evidence (Confidence Tier, null
    counts, notes) as read-only hints for the reviewer; later slices surface
    them for `low`/`ambiguous` acknowledgement (#151).
    """

    source: str
    dest: str
    type: str
    required: bool = True
    confidence: str = "high"
    null_count: int = 0
    total_seen: int = 0
    notes: List[str] = field(default_factory=list)


@dataclass
class PipelineConfigDraft:
    """An editable, in-memory Pipeline Config grounded in a sample File."""

    dest_table: str
    columns: List[ColumnDraft]
    fmt: str = "csv"
    write_mode: str = "append"

    @classmethod
    def from_sample(
        cls,
        file: str,
        dest_table: str,
        *,
        fmt: str = "csv",
        sample_rows: int = 1000,
        encoding: Optional[str] = None,
    ) -> "PipelineConfigDraft":
        """Run Schema Inference over a sample File and seed an editable draft.

        Schema Inference is delegated to `AuthoringSession`; each inferred column
        becomes a `ColumnDraft` whose `dest` defaults to the source name and
        which is `required` by default (the reviewer relaxes it per Column
        Tolerance). The Confidence Tier and inference notes ride along as
        read-only evidence.
        """
        if fmt != "csv":
            raise ValueError(
                "PipelineConfigDraft currently supports CSV samples only; "
                "multi-format authoring ships in a later slice."
            )
        inferred = AuthoringSession(file, fmt, encoding=encoding).infer_schema(
            sample_rows=sample_rows
        )
        columns = [
            ColumnDraft(
                source=c.name,
                dest=c.name,
                type=c.inferred_type,
                required=True,
                confidence=c.confidence,
                null_count=c.null_count,
                total_seen=c.total_seen,
                notes=list(c.notes),
            )
            for c in inferred
        ]
        return cls(dest_table=dest_table, columns=columns, fmt=fmt)

    def column(self, source: str) -> ColumnDraft:
        """Return the column whose current source name matches, or raise."""
        for c in self.columns:
            if c.source == source:
                return c
        raise KeyError(f"No column with source {source!r} in the draft.")

    def edit_column(
        self,
        source: str,
        *,
        new_source: Optional[str] = None,
        dest: Optional[str] = None,
        type: Optional[str] = None,
        required: Optional[bool] = None,
    ) -> ColumnDraft:
        """Edit one column's authored fields, validating the Column Type.

        `source` selects the column by its current source name. An invalid
        Column Type raises before any field is mutated, so a rejected edit
        leaves the draft unchanged.
        """
        col = self.column(source)
        if type is not None:
            validate_column_type(type)
            col.type = type
        if new_source is not None:
            col.source = new_source
        if dest is not None:
            col.dest = dest
        if required is not None:
            col.required = required
        return col

    def to_config_dict(self) -> dict:
        """Emit a Pipeline Config mapping for this draft."""
        return {
            "format": self.fmt,
            "dest_table": self.dest_table,
            "write_mode": self.write_mode,
            "connector": dict(_PLACEHOLDER_CONNECTOR),
            "columns": [
                {
                    "source": c.source,
                    "dest": c.dest,
                    "type": c.type,
                    "required": c.required,
                }
                for c in self.columns
            ],
        }

    def to_config(self) -> PipelineConfig:
        """Round-trip the draft through the existing config loading rules."""
        return config_from_dict(self.to_config_dict())
