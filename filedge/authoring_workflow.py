"""Headless Authoring Workflow orchestration for the Authoring UI.

The Textual shell delegates here for every domain operation: preview, Schema
Inference-backed draft creation, Authoring Validation, Pipeline Folder writing,
and Pipeline Registry updates. This module performs no Run, opens no Audit DB,
and reads no secret material.
"""

from dataclasses import dataclass, field
from typing import Optional

import os

from filedge.authoring import AuthoringSession
from filedge.authoring_draft import (
    ColumnDraft,
    EncryptDraft,
    HashDraft,
    PipelineConfigDraft,
    draft_from_config,
)
from filedge.authoring_validation import AuthoringValidationReport, validate_authoring
from filedge.config import load_config
from filedge.connectors import (
    ConnectorDescriptor,
    CredentialPlaceholder,
    available_connector_types,
    connector_descriptor,
)
from filedge.file_sample import FormatNotDetected, resolve_format, read_excel_sheet_names
from filedge.pipeline_folder import (
    CONFIG_FILENAME,
    RUNBOOK_FILENAME,
    PipelineFolderResult,
    operator_handoff_commands,
    read_runbook_sample_file,
    write_pipeline_folder,
)
from filedge.pipeline_registry import RegistryEntry, load_registry


RISKY_CONFIDENCE_TIERS = {"low", "ambiguous"}


@dataclass(frozen=True)
class ConfidenceTierReview:
    """One low/ambiguous Confidence Tier decision surfaced for review."""

    source: str
    dest: str
    confidence: str
    evidence: str
    acknowledged: bool = False


@dataclass
class AuthoringWorkflow:
    """One in-memory Authoring Workflow for a single sample File."""

    file: str
    workspace: str
    dest_table: str
    fmt: str
    sample_rows: int = 1000
    encoding: Optional[str] = None
    sheet: Optional[object] = None
    out: Optional[str] = None
    draft: Optional[PipelineConfigDraft] = None
    validation_report: Optional[AuthoringValidationReport] = None
    generated: Optional[PipelineFolderResult] = None
    preview_rows: list[dict] = field(default_factory=list)
    excel_sheets: list[str] = field(default_factory=list)
    confidence_acknowledgements: dict[str, str] = field(default_factory=dict)
    # Re-author state (#173): set by open_folder, drives save-back in place.
    reauthor: bool = False
    registry_entry: Optional[RegistryEntry] = None

    @classmethod
    def open_folder(
        cls,
        *,
        folder: str,
        workspace: str,
        sample_rows: int = 1000,
    ) -> "AuthoringWorkflow":
        """Open an existing Pipeline Folder to re-author its Pipeline Config.

        Loads the Folder's ``pipeline.yaml`` back into a Pipeline Config Draft
        (the re-author entry point, #173) and recovers the sample File path from
        the Authoring Runbook. No Schema Inference is run and no fresh sample is
        chosen here — the loaded draft is editable as-is (#174 adds the refresh).
        """
        folder_abs = os.path.join(workspace, folder)
        config_path = os.path.join(folder_abs, CONFIG_FILENAME)
        runbook_path = os.path.join(folder_abs, RUNBOOK_FILENAME)

        config = load_config(config_path)
        draft = draft_from_config(config)

        sample_file = None
        if os.path.isfile(runbook_path):
            with open(runbook_path) as f:
                sample_file = read_runbook_sample_file(f.read())

        registry = load_registry(workspace)
        registry_entry = next(
            (e for e in registry.entries if e.folder == folder), None
        )

        workflow = cls(
            file=sample_file or "",
            workspace=workspace,
            dest_table=config.dest_table,
            fmt=config.format,
            sample_rows=sample_rows,
            reauthor=True,
            registry_entry=registry_entry,
        )
        workflow.draft = draft
        return workflow

    @classmethod
    def start(
        cls,
        *,
        file: str,
        workspace: str,
        dest_table: str,
        fmt: Optional[str] = None,
        sample_rows: int = 1000,
        encoding: Optional[str] = None,
        sheet: Optional[object] = None,
        out: Optional[str] = None,
    ) -> "AuthoringWorkflow":
        """Resolve format, read preview rows, and seed the Pipeline Config Draft."""
        resolved = resolve_format(file, fmt)
        if isinstance(resolved, FormatNotDetected):
            raise ValueError(
                f"Cannot detect format for {resolved.file!r}; pass --format."
            )

        sheets: list[str] = []
        concrete_sheet = sheet
        if resolved == "excel":
            sheets = read_excel_sheet_names(file)
            if concrete_sheet is None:
                concrete_sheet = sheets[0]

        workflow = cls(
            file=file,
            workspace=workspace,
            dest_table=dest_table,
            fmt=resolved,
            sample_rows=sample_rows,
            encoding=encoding,
            sheet=concrete_sheet,
            out=out,
            excel_sheets=sheets,
        )
        if resolved != "fixed_width":
            workflow.draft = PipelineConfigDraft.from_sample(
                file,
                dest_table,
                fmt=resolved,
                sample_rows=sample_rows,
                encoding=encoding,
                sheet=concrete_sheet,
            )
            workflow.preview_rows = workflow._session().preview(num_rows=5)
        return workflow

    def choose_format(self, fmt: str) -> None:
        """Apply an explicit format override and re-seed the draft."""
        self._reseed(fmt=fmt, sheet=self.sheet if fmt == "excel" else None)

    def choose_sheet(self, sheet: object) -> None:
        """Select an Excel sheet and re-seed the draft from that worksheet."""
        if self.fmt != "excel":
            raise ValueError("A sheet picker is only valid for excel sample Files.")
        self._reseed(fmt="excel", sheet=sheet)

    def _reseed(self, *, fmt: str, sheet: object) -> None:
        """Rebuild the draft from the sample File under a new format/sheet.

        Identity fields (file, workspace, dest_table, sample_rows, encoding, out)
        are unchanged. Everything derived from the sample File is reset: the
        resolved format, the draft, the preview, the Excel sheet list, the
        validation report, any generated artifacts, and the Confidence Tier
        acknowledgements — re-seeding a different format invalidates prior review.
        """
        rebuilt = self.start(
            file=self.file,
            workspace=self.workspace,
            dest_table=self.dest_table,
            fmt=fmt,
            sample_rows=self.sample_rows,
            encoding=self.encoding,
            sheet=sheet,
            out=self.out,
        )
        self.fmt = rebuilt.fmt
        self.sheet = rebuilt.sheet
        self.excel_sheets = rebuilt.excel_sheets
        self.draft = rebuilt.draft
        self.preview_rows = rebuilt.preview_rows
        self.validation_report = None
        self.generated = None
        self.confidence_acknowledgements = {}

    def default_sample_file(self) -> Optional[str]:
        """The Runbook-recorded sample File when it still exists on disk (#174).

        The re-author sample picker defaults to this; it returns ``None`` when
        the original sample is gone so the caller knows to prompt instead.
        """
        if self.file and os.path.isfile(self.file):
            return self.file
        return None

    def refresh_sample(self, sample_file: str) -> None:
        """Re-run Schema Inference against a fresh sample and refresh the
        Confidence Tier and inference evidence on loaded columns (#174).

        Only the read-only evidence (confidence, null_count, total_seen, notes)
        is updated, matched by source name. The authored fields — type, dest,
        required — are never overwritten by the refresh. Setting the sample File
        means a later :meth:`generate` records the new sample in the regenerated
        Authoring Runbook. Inferred columns absent from the draft are ignored;
        loaded columns absent from the new sample keep their prior evidence.
        """
        draft = self._require_draft()
        inferred = AuthoringSession(
            sample_file, self.fmt, encoding=self.encoding, sheet=self.sheet
        ).infer_schema(sample_rows=self.sample_rows)
        by_source = {c.name: c for c in inferred}
        for column in draft.columns:
            evidence = by_source.get(column.source)
            if evidence is None:
                continue
            column.confidence = evidence.confidence
            column.null_count = evidence.null_count
            column.total_seen = evidence.total_seen
            column.notes = list(evidence.notes)
        self.file = sample_file
        self.preview_rows = self._session().preview(num_rows=5)
        self._mutated()

    def set_fixed_width_layout(self, columns: list[ColumnDraft]) -> None:
        """Populate the manual Fixed-Width Layout entry surface."""
        if self.fmt != "fixed_width":
            raise ValueError("Fixed-Width Layout is only valid for fixed_width.")
        self.draft = PipelineConfigDraft.from_fixed_width_layout(
            self.dest_table, columns
        )
        self.preview_rows = self._session().preview(num_rows=5)
        self.confidence_acknowledgements.clear()

    def write_modes(self) -> list[str]:
        """Return Write Modes available during Pipeline Authoring."""
        return ["append", "truncate", "cdc"]

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
        """Edit one draft column's authored fields through the Workflow seam.

        Every draft mutation funnels through the Workflow so the
        validation-staleness invariant holds in one place; the Authoring UI must
        not reach past this seam into the draft.
        """
        column = self._require_draft().edit_column(
            source,
            new_source=new_source,
            dest=dest,
            type=type,
            required=required,
            start=start,
            width=width,
        )
        self._mutated()
        return column

    def choose_write_mode(self, write_mode: str) -> None:
        """Select append, truncate, or cdc Write Mode."""
        self._require_draft().choose_write_mode(write_mode)
        self._mutated()

    def set_cdc_settings(
        self,
        *,
        business_keys: list[str] | None = None,
        sequence_by: str | None = None,
    ) -> None:
        """Record CDC File business key and sequence column settings."""
        self._require_draft().set_cdc_settings(
            business_keys=business_keys,
            sequence_by=sequence_by,
        )
        self._mutated()

    def connector_types(self) -> list[str]:
        """Return Connector Registry types available to the Authoring UI."""
        return available_connector_types()

    def connector_descriptor(self) -> ConnectorDescriptor:
        """Return authoring-safe metadata for the selected Connector."""
        return connector_descriptor(self._require_draft().connector_type)

    def choose_connector(self, connector_type: str) -> ConnectorDescriptor:
        """Select a Connector and seed its non-secret settings."""
        draft = self._require_draft()
        draft.choose_connector(connector_type)
        self._mutated()
        return connector_descriptor(draft.connector_type)

    def set_connector_setting(self, name: str, value: str) -> None:
        """Record one required non-secret Connector setting."""
        self._require_draft().set_connector_setting(name, value)
        self._mutated()

    def set_field_encryption(
        self,
        dest: str,
        *,
        encrypt_key: Optional[str] = None,
        hash_key: Optional[str] = None,
    ) -> None:
        """Declare AES-256-GCM encrypt and/or HMAC-SHA256 hash on a column.

        Keys are Credential Placeholder references (``env:NAME`` or
        ``secrets:/abs/path``). The UI never collects, stores, tests, or exports
        key material.
        """
        draft = self._require_draft()
        encrypt = EncryptDraft(key=encrypt_key) if encrypt_key else None
        hash_block = HashDraft(key=hash_key) if hash_key else None
        draft.set_field_encryption(dest, encrypt=encrypt, hash=hash_block)
        self._mutated()

    def clear_field_encryption(
        self,
        dest: str,
        *,
        encrypt: bool = False,
        hash: bool = False,
    ) -> None:
        """Remove the encrypt or hash declaration from a destination column."""
        self._require_draft().clear_field_encryption(
            dest, encrypt=encrypt, hash=hash
        )
        self._mutated()

    def duplicate_column(self, dest: str, *, new_dest: str) -> ColumnDraft:
        """Clone a column under a new dest so one source maps to two destinations."""
        cloned = self._require_draft().duplicate_column(dest, new_dest=new_dest)
        self._mutated()
        return cloned

    def field_encryption_declarations(self) -> list[dict]:
        """List declared encrypt/hash columns for the Authoring Runbook.

        Returns Credential Placeholder references only — never resolved key
        material — so this list is safe to render in the non-secret Runbook.
        """
        if self.draft is None:
            return []
        items: list[dict] = []
        for column in self.draft.field_encryption_columns():
            entry: dict = {"source": column.source, "dest": column.dest}
            if column.encrypt is not None:
                entry["encrypt"] = {
                    "algorithm": column.encrypt.algorithm,
                    "key": column.encrypt.key,
                }
            if column.hash is not None:
                entry["hash"] = {
                    "algorithm": column.hash.algorithm,
                    "key": column.hash.key,
                }
            items.append(entry)
        return items

    def credential_placeholders(self) -> list[CredentialPlaceholder]:
        """Return runtime Credential Placeholders for Connector and FE keys."""
        placeholders = list(self.connector_descriptor().credential_placeholders)
        seen = {p.env_var for p in placeholders}
        for item in self.field_encryption_declarations():
            for kind in ("encrypt", "hash"):
                block = item.get(kind)
                if not block:
                    continue
                key = block["key"]
                name = key[len("env:"):] if key.startswith("env:") else key
                if name in seen:
                    continue
                seen.add(name)
                placeholders.append(
                    CredentialPlaceholder(
                        name,
                        (
                            f"Field Encryption {kind} key for destination "
                            f"{item['dest']}"
                        ),
                    )
                )
        return placeholders

    def confidence_reviews(self) -> list[ConfidenceTierReview]:
        """List risky Confidence Tiers and whether each was acknowledged."""
        if self.draft is None:
            return []
        return [
            ConfidenceTierReview(
                source=c.source,
                dest=c.dest,
                confidence=c.confidence,
                evidence=_confidence_evidence(c),
                acknowledged=c.source in self.confidence_acknowledgements,
            )
            for c in self.draft.columns
            if c.confidence in RISKY_CONFIDENCE_TIERS
        ]

    def acknowledge_confidence_tier(self, source: str) -> ConfidenceTierReview:
        """Record reviewer acknowledgement for one risky Confidence Tier."""
        for review in self.confidence_reviews():
            if review.source == source:
                self.confidence_acknowledgements[source] = review.evidence
                return ConfidenceTierReview(
                    source=review.source,
                    dest=review.dest,
                    confidence=review.confidence,
                    evidence=review.evidence,
                    acknowledged=True,
                )
        raise ValueError(
            f"No low or ambiguous Confidence Tier column named {source!r}."
        )

    def unacknowledged_confidence_reviews(self) -> list[ConfidenceTierReview]:
        """Risky Confidence Tier decisions still blocking artifact generation."""
        return [r for r in self.confidence_reviews() if not r.acknowledged]

    def validate(self) -> AuthoringValidationReport:
        """Run Authoring Validation for the current draft."""
        draft = self._require_draft()
        self.validation_report = validate_authoring(
            self.file,
            draft.to_config(),
            encoding=self.encoding,
            sheet=self.sheet,
            sample_rows=self.sample_rows,
        )
        return self.validation_report

    def generate(self) -> PipelineFolderResult:
        """Write the Pipeline Folder and update the Pipeline Registry."""
        draft = self._require_draft()
        missing = draft.required_connector_settings_missing()
        if missing:
            raise ValueError(
                "Required non-secret Connector settings are missing: "
                + ", ".join(missing)
            )
        unacknowledged = self.unacknowledged_confidence_reviews()
        if unacknowledged:
            columns = ", ".join(r.source for r in unacknowledged)
            raise ValueError(
                "Every low or ambiguous Confidence Tier must be acknowledged "
                f"before generation: {columns}."
            )
        report = self.validation_report or self.validate()
        if not report.ok:
            raise ValueError("Authoring Validation must be green before generation.")
        # Re-author save-back rewrites the existing Folder in place and preserves
        # the Registry references recorded when the Pipeline was first authored.
        entry = self.registry_entry if self.reauthor else None
        self.generated = write_pipeline_folder(
            self.workspace,
            draft.to_config_dict(),
            sample_file=self.file,
            out=entry.id if entry is not None else self.out,
            watched_directory=entry.watched_directory if entry is not None else None,
            audit_db=entry.audit_db if entry is not None else None,
            audit_export=entry.audit_export if entry is not None else None,
            overwrite=self.reauthor,
            confidence_acknowledgements=[
                {
                    "source": r.source,
                    "dest": r.dest,
                    "confidence": r.confidence,
                    "evidence": r.evidence,
                }
                for r in self.confidence_reviews()
                if r.acknowledged
            ],
            credential_placeholders=[
                {
                    "env_var": p.env_var,
                    "purpose": p.purpose,
                }
                for p in self.credential_placeholders()
            ],
            field_encryption_columns=self.field_encryption_declarations(),
        )
        return self.generated

    def suggested_commands(self) -> list[str]:
        """Return the post-generation Operator CLI handoff commands.

        Renders the same handoff the Authoring Runbook records, from the values
        the Pipeline Folder writer actually persisted — one source of truth for
        the command set and the Audit DB shell reference.
        """
        if self.generated is None:
            return []
        return operator_handoff_commands(
            sample_file=self.file,
            config_path=f"{self.generated.folder}/pipeline.yaml",
            watched_directory=self.generated.watched_directory,
            audit_db=self.generated.audit_db,
            audit_export=self.generated.audit_export,
        )

    def _session(self) -> AuthoringSession:
        draft = self.draft
        return AuthoringSession(
            self.file,
            self.fmt,
            config=draft.to_config() if draft is not None else None,
            encoding=self.encoding,
            sheet=self.sheet,
        )

    def _mutated(self) -> None:
        """Mark the draft changed since the last Authoring Validation.

        Every Workflow mutator funnels through here, so the rule "an edit
        invalidates validation" lives in one place instead of being repeated —
        and forgettable — at each mutation site.
        """
        self.validation_report = None

    def _require_draft(self) -> PipelineConfigDraft:
        if self.draft is None:
            raise ValueError("Authoring Workflow needs a Pipeline Config Draft.")
        return self.draft


def _confidence_evidence(column: ColumnDraft) -> str:
    parts = [
        f"null_count={column.null_count}",
        f"total_seen={column.total_seen}",
    ]
    if column.notes:
        parts.append("notes=" + "; ".join(column.notes))
    return ", ".join(parts)
