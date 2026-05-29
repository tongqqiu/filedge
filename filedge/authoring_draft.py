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
from filedge.connectors import connector_descriptor


WRITE_MODES = ("append", "truncate", "cdc")
DEFAULT_CDC_OPERATION_COLUMN = "op"
DEFAULT_CDC_OPERATIONS = {
    "insert": ["c", "insert"],
    "update": ["u", "update"],
    "delete": ["d", "delete"],
}

ENCRYPT_ALGORITHM = "aes-256-gcm"
HASH_ALGORITHM = "hmac-sha256"


@dataclass
class EncryptDraft:
    """Per-column AES-256-GCM Field Encryption declaration (ADR-0014).

    `key` is a Credential Placeholder reference (``env:NAME`` or
    ``secrets:/abs/path``); the Authoring UI never collects, stores, or reads
    the resolved key material.
    """

    key: str
    algorithm: str = ENCRYPT_ALGORITHM


@dataclass
class HashDraft:
    """Per-column HMAC-SHA256 Field Encryption hash declaration (ADR-0014)."""

    key: str
    algorithm: str = HASH_ALGORITHM


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
    encrypt: Optional[EncryptDraft] = None
    hash: Optional[HashDraft] = None


@dataclass
class PipelineConfigDraft:
    """An editable, in-memory Pipeline Config grounded in a sample File."""

    dest_table: str
    columns: List[ColumnDraft]
    fmt: str = "csv"
    write_mode: str = "append"
    sheet: Optional[object] = None
    cdc_keys: List[str] = field(default_factory=list)
    cdc_sequence_by: str = ""
    cdc_operation_column: str = DEFAULT_CDC_OPERATION_COLUMN
    connector_type: str = "sqlite"
    connector_options: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.connector_options:
            self.choose_connector(self.connector_type)

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

    def choose_connector(self, connector_type: str) -> None:
        """Select a Connector Registry type and seed its non-secret defaults."""
        descriptor = connector_descriptor(connector_type)
        self.connector_type = descriptor.type
        self.connector_options = {
            setting.name: setting.default
            for setting in descriptor.settings
            if setting.default
        }

    def set_connector_setting(self, name: str, value: str) -> None:
        """Set one non-secret connector setting exposed by the Registry."""
        descriptor = connector_descriptor(self.connector_type)
        known = {setting.name for setting in descriptor.settings}
        if name not in known:
            raise ValueError(
                f"Connector {self.connector_type!r} has no setting named {name!r}."
            )
        self.connector_options[name] = value

    def column_by_dest(self, dest: str) -> ColumnDraft:
        """Return the column whose destination name matches, or raise."""
        for c in self.columns:
            if c.dest == dest:
                return c
        raise KeyError(f"No column with dest {dest!r} in the draft.")

    def duplicate_column(self, dest: str, *, new_dest: str) -> ColumnDraft:
        """Clone an existing column under a new destination name.

        Supports the Field Encryption pattern where one source column produces
        two destination columns (one encrypted, one hashed). Schema Inference
        evidence is preserved; the new column starts with no encrypt/hash
        declarations.
        """
        original = self.column_by_dest(dest)
        if any(c.dest == new_dest for c in self.columns):
            raise ValueError(f"A column with dest {new_dest!r} already exists.")
        clone = ColumnDraft(
            source=original.source,
            dest=new_dest,
            type=original.type,
            required=original.required,
            confidence=original.confidence,
            null_count=original.null_count,
            total_seen=original.total_seen,
            notes=list(original.notes),
            start=original.start,
            width=original.width,
        )
        self.columns.append(clone)
        return clone

    def set_field_encryption(
        self,
        dest: str,
        *,
        encrypt: Optional[EncryptDraft] = None,
        hash: Optional[HashDraft] = None,
    ) -> ColumnDraft:
        """Declare an `encrypt:` and/or `hash:` block on a destination column.

        Each call replaces the corresponding block. Pass ``None`` to leave the
        existing block untouched; use :meth:`clear_field_encryption` to remove
        a block. Key material is never collected — `encrypt.key` and `hash.key`
        are Credential Placeholder references resolved at runtime.
        """
        col = self.column_by_dest(dest)
        if encrypt is not None:
            col.encrypt = encrypt
        if hash is not None:
            col.hash = hash
        return col

    def clear_field_encryption(
        self,
        dest: str,
        *,
        encrypt: bool = False,
        hash: bool = False,
    ) -> ColumnDraft:
        """Remove the `encrypt:` and/or `hash:` block from a destination column."""
        col = self.column_by_dest(dest)
        if encrypt:
            col.encrypt = None
        if hash:
            col.hash = None
        return col

    def field_encryption_columns(self) -> list[ColumnDraft]:
        """Destination columns that declare an encrypt or hash block."""
        return [c for c in self.columns if c.encrypt is not None or c.hash is not None]

    def required_connector_settings_missing(self) -> list[str]:
        """Return required non-secret Connector settings that still need values."""
        descriptor = connector_descriptor(self.connector_type)
        return [
            setting.name
            for setting in descriptor.settings
            if setting.required and not self.connector_options.get(setting.name)
        ]

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

    def choose_write_mode(self, write_mode: str) -> None:
        """Select the Write Mode for generated Pipeline Config."""
        if write_mode not in WRITE_MODES:
            raise ValueError(
                f"Write Mode must be one of {', '.join(WRITE_MODES)}, got {write_mode!r}."
            )
        self.write_mode = write_mode

    def set_cdc_settings(
        self,
        *,
        business_keys: Optional[List[str]] = None,
        sequence_by: Optional[str] = None,
    ) -> None:
        """Set CDC-specific Write Mode fields captured by the Authoring UI."""
        if business_keys is not None:
            self.cdc_keys = [key for key in business_keys if key]
        if sequence_by is not None:
            self.cdc_sequence_by = sequence_by

    def to_config_dict(self) -> dict:
        """Emit a Pipeline Config mapping for this draft."""
        data = {
            "format": self.fmt,
            "dest_table": self.dest_table,
            "write_mode": self.write_mode,
            "connector": {
                "type": self.connector_type,
                **{
                    key: value
                    for key, value in self.connector_options.items()
                    if value != ""
                },
            },
            "columns": [self._column_config(c) for c in self.columns],
        }
        if self.fmt == "excel":
            data["excel"] = {"sheet": self.sheet}
        if self.write_mode == "cdc":
            data["cdc"] = {
                "keys": list(self.cdc_keys),
                "operation_column": self.cdc_operation_column,
                "sequence_by": self.cdc_sequence_by,
                "operations": {
                    key: list(values)
                    for key, values in DEFAULT_CDC_OPERATIONS.items()
                },
            }
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
        if column.encrypt is not None:
            data["encrypt"] = {
                "algorithm": column.encrypt.algorithm,
                "key": column.encrypt.key,
            }
        if column.hash is not None:
            data["hash"] = {
                "algorithm": column.hash.algorithm,
                "key": column.hash.key,
            }
        return data


def draft_from_config(config: PipelineConfig) -> PipelineConfigDraft:
    """Load an existing PipelineConfig back into an editable Draft (#172).

    Confidence Tier is set to ``"loaded"`` on all columns to distinguish
    loaded-but-unverified columns from inference-backed ones (``"high"``,
    ``"low"``, ``"ambiguous"``). Schema Inference refresh is issue #174.

    Only the minimal supported shape is accepted: CSV format, ``append`` write
    mode, ``sqlite`` connector, no Field Encryption blocks. Unsupported shapes
    raise ``ValueError`` naming the offending field so future slices can opt
    them in deliberately.
    """
    if config.format != "csv":
        raise ValueError(
            f"draft_from_config does not yet support format {config.format!r}; "
            "only 'csv' is supported in this slice."
        )
    connector_type = config.connector.type if config.connector else "sqlite"
    if connector_type != "sqlite":
        raise ValueError(
            f"draft_from_config does not yet support connector {connector_type!r}; "
            "only 'sqlite' is supported in this slice."
        )
    if config.write_mode != "append":
        raise ValueError(
            f"draft_from_config does not yet support write_mode {config.write_mode!r}; "
            "only 'append' is supported in this slice."
        )
    for col in config.columns:
        if col.encrypt is not None:
            raise ValueError(
                f"draft_from_config does not yet support Field Encryption "
                f"(column {col.dest!r} has an encrypt: block)."
            )
        if col.hash is not None:
            raise ValueError(
                f"draft_from_config does not yet support Field Encryption "
                f"(column {col.dest!r} has a hash: block)."
            )

    columns = [
        ColumnDraft(
            source=col.source,
            dest=col.dest,
            type=col.type,
            required=col.required,
            confidence="loaded",
            null_count=0,
            total_seen=0,
            notes=[],
        )
        for col in config.columns
    ]
    connector_options = dict(config.connector.options) if config.connector else {}
    # Bypass __post_init__ (which re-seeds connector defaults from the Registry)
    # so the loaded options from pipeline.yaml are preserved verbatim.
    draft = PipelineConfigDraft.__new__(PipelineConfigDraft)
    draft.dest_table = config.dest_table
    draft.columns = columns
    draft.fmt = config.format
    draft.write_mode = config.write_mode
    draft.sheet = None
    draft.cdc_keys = []
    draft.cdc_sequence_by = ""
    draft.cdc_operation_column = DEFAULT_CDC_OPERATION_COLUMN
    draft.connector_type = connector_type
    draft.connector_options = connector_options
    return draft
