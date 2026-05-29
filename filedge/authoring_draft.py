"""The Pipeline Config Draft — the editable core of the Authoring Workflow.

`PipelineConfigDraft` is the headless data model the Authoring UI (ADR-0016)
will sit on top of: build it from a sample File, run Schema Inference once,
review and edit the per-column source name, destination name, Column Type, and
required flag, then emit a Pipeline Config that round-trips through the same
config loading the Operator CLI uses. It reuses `AuthoringSession` for Schema
Inference and `config_from_dict` for the round-trip; it reimplements no domain
rule, holds no secrets, and writes nothing to disk (ADR-0015).

This draft stays deliberately headless: the Authoring UI edits it, but Parser
dispatch, Schema Inference, config loading, and Fixed-Width Layout validation
remain in the same modules the Operator CLI uses.
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
    start: Optional[int] = None
    width: Optional[int] = None


@dataclass
class PipelineConfigDraft:
    """An editable, in-memory Pipeline Config grounded in a sample File."""

    dest_table: str
    columns: List[ColumnDraft]
    fmt: str = "csv"
    write_mode: str = "append"
    sheet: Optional[object] = None

    @classmethod
    def from_sample(
        cls,
        file: str,
        dest_table: str,
        *,
        fmt: str = "csv",
        sample_rows: int = 1000,
        encoding: Optional[str] = None,
        sheet: Optional[object] = None,
    ) -> "PipelineConfigDraft":
        """Run Schema Inference over a sample File and seed an editable draft.

        Schema Inference is delegated to `AuthoringSession`; each inferred column
        becomes a `ColumnDraft` whose `dest` defaults to the source name and
        which is `required` by default (the reviewer relaxes it per Column
        Tolerance). The Confidence Tier and inference notes ride along as
        read-only evidence.
        """
        if fmt == "fixed_width":
            raise ValueError(
                "fixed_width authoring requires an explicit Fixed-Width Layout; "
                "Schema Inference is not available for fixed-width Files."
            )
        inferred = AuthoringSession(
            file, fmt, encoding=encoding, sheet=sheet
        ).infer_schema(sample_rows=sample_rows)
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
        return cls(dest_table=dest_table, columns=columns, fmt=fmt, sheet=sheet)

    @classmethod
    def from_fixed_width_layout(
        cls,
        dest_table: str,
        columns: List[ColumnDraft],
    ) -> "PipelineConfigDraft":
        """Seed a draft from an explicit Fixed-Width Layout entry surface."""
        draft = cls(dest_table=dest_table, columns=[], fmt="fixed_width")
        for c in columns:
            draft.add_fixed_width_column(
                source=c.source,
                dest=c.dest,
                type=c.type,
                start=c.start,
                width=c.width,
                required=c.required,
            )
        return draft

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
        start: Optional[int] = None,
        width: Optional[int] = None,
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
        if start is not None:
            col.start = start
        if width is not None:
            col.width = width
        return col

    def add_fixed_width_column(
        self,
        *,
        source: str,
        dest: str,
        type: str,
        start: int,
        width: int,
        required: bool = True,
    ) -> ColumnDraft:
        """Append one authored Fixed-Width Layout row."""
        if self.fmt != "fixed_width":
            raise ValueError("Fixed-Width Layout columns are only valid for fixed_width.")
        validate_column_type(type)
        col = ColumnDraft(
            source=source,
            dest=dest,
            type=type,
            required=required,
            confidence="manual",
            notes=["Fixed-Width Layout entered manually; Schema Inference skipped."],
            start=start,
            width=width,
        )
        self.columns.append(col)
        return col

    def to_config_dict(self) -> dict:
        """Emit a Pipeline Config mapping for this draft."""
        data = {
            "format": self.fmt,
            "dest_table": self.dest_table,
            "write_mode": self.write_mode,
            "connector": dict(_PLACEHOLDER_CONNECTOR),
            "columns": [self._column_config(c) for c in self.columns],
        }
        if self.fmt == "excel":
            data["excel"] = {"sheet": self.sheet}
        return data

    def to_config(self) -> PipelineConfig:
        """Round-trip the draft through the existing config loading rules."""
        return config_from_dict(self.to_config_dict())

    def _column_config(self, column: ColumnDraft) -> dict:
        data = {
            "source": column.source,
            "dest": column.dest,
            "type": column.type,
            "required": column.required,
        }
        if self.fmt == "fixed_width":
            data["start"] = column.start
            data["width"] = column.width
        return data
