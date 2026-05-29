"""Headless Authoring Workflow orchestration for the Authoring UI.

The Textual shell delegates here for every domain operation: preview, Schema
Inference-backed draft creation, Authoring Validation, Pipeline Folder writing,
and Pipeline Registry updates. This module performs no Run, opens no Audit DB,
and reads no secret material.
"""

from dataclasses import dataclass, field
from typing import Optional

from filedge.authoring import AuthoringSession
from filedge.authoring_draft import ColumnDraft, PipelineConfigDraft
from filedge.authoring_validation import AuthoringValidationReport, validate_authoring
from filedge.connectors import (
    ConnectorDescriptor,
    CredentialPlaceholder,
    available_connector_types,
    connector_descriptor,
)
from filedge.file_sample import FormatNotDetected, resolve_format, read_excel_sheet_names
from filedge.pipeline_folder import PipelineFolderResult, write_pipeline_folder


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
        """Apply an explicit format override and rebuild the draft."""
        rebuilt = self.start(
            file=self.file,
            workspace=self.workspace,
            dest_table=self.dest_table,
            fmt=fmt,
            sample_rows=self.sample_rows,
            encoding=self.encoding,
            sheet=self.sheet if fmt == "excel" else None,
            out=self.out,
        )
        self.__dict__.update(rebuilt.__dict__)

    def choose_sheet(self, sheet: object) -> None:
        """Select an Excel sheet and rebuild the draft from that worksheet."""
        if self.fmt != "excel":
            raise ValueError("A sheet picker is only valid for excel sample Files.")
        rebuilt = self.start(
            file=self.file,
            workspace=self.workspace,
            dest_table=self.dest_table,
            fmt="excel",
            sample_rows=self.sample_rows,
            encoding=self.encoding,
            sheet=sheet,
            out=self.out,
        )
        self.__dict__.update(rebuilt.__dict__)

    def set_fixed_width_layout(self, columns: list[ColumnDraft]) -> None:
        """Populate the manual Fixed-Width Layout entry surface."""
        if self.fmt != "fixed_width":
            raise ValueError("Fixed-Width Layout is only valid for fixed_width.")
        self.draft = PipelineConfigDraft.from_fixed_width_layout(
            self.dest_table, columns
        )
        self.preview_rows = self._session().preview(num_rows=5)
        self.confidence_acknowledgements.clear()

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
        self.validation_report = None
        return connector_descriptor(draft.connector_type)

    def set_connector_setting(self, name: str, value: str) -> None:
        """Record one required non-secret Connector setting."""
        self._require_draft().set_connector_setting(name, value)
        self.validation_report = None

    def credential_placeholders(self) -> list[CredentialPlaceholder]:
        """Return runtime Credential Placeholders for the selected Connector."""
        return list(self.connector_descriptor().credential_placeholders)

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
        self.generated = write_pipeline_folder(
            self.workspace,
            draft.to_config_dict(),
            sample_file=self.file,
            out=self.out,
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
        )
        return self.generated

    def suggested_commands(self) -> list[str]:
        """Return the post-generation Operator CLI handoff commands."""
        if self.generated is None:
            return []
        config = f"{self.generated.folder}/pipeline.yaml"
        audit_db = self._audit_db_ref()
        return [
            f"filedge validate {self.file} --config {config}",
            f"filedge healthcheck --config {config} --audit-db-url {audit_db}",
            f"filedge run --dir ./landing/{self.generated.pipeline_id} "
            f"--config {config} --audit-db-url {audit_db}",
            f"filedge export-audit --audit-db-url {audit_db} "
            f"--output ./audit-exports/{self.generated.pipeline_id}/index.html",
        ]

    def _session(self) -> AuthoringSession:
        draft = self.draft
        return AuthoringSession(
            self.file,
            self.fmt,
            config=draft.to_config() if draft is not None else None,
            encoding=self.encoding,
            sheet=self.sheet,
        )

    def _require_draft(self) -> PipelineConfigDraft:
        if self.draft is None:
            raise ValueError("Authoring Workflow needs a Pipeline Config Draft.")
        return self.draft

    def _audit_db_ref(self) -> str:
        if self.generated is None:
            return ""
        env_name = self.generated.pipeline_id.upper().replace("-", "_")
        return f"${{{env_name}_AUDIT_DB_URL}}"


def _confidence_evidence(column: ColumnDraft) -> str:
    parts = [
        f"null_count={column.null_count}",
        f"total_seen={column.total_seen}",
    ]
    if column.notes:
        parts.append("notes=" + "; ".join(column.notes))
    return ", ".join(parts)
