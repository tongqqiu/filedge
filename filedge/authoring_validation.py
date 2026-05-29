"""Authoring Validation — the headless compatibility check behind the Authoring
Workflow (CONTEXT.md: Authoring Validation, Validation Scope).

`validate_authoring` takes a sample File and a Pipeline Config and returns a
structured, non-mutating report describing whether the two agree under the
Validation Scope: Parser readability, Column Tolerance, Strict Mode type
coercion, structural Field Encryption validity, Write Mode required settings,
and Pipeline Config loading. It reuses the existing `AuthoringSession`, the
`validate_file` Strict Mode check, and the already-parsed Pipeline Config; it
reimplements no parsing or coercion rule.

It is deliberately a deep module: a small interface (a File, a Config, one call)
over the six-dimensional compatibility judgement an Authoring UI surfaces on top.
By contract it runs no Run, instantiates no Connector, touches no Audit DB, and
makes no Destination reachability check — those belong to `filedge healthcheck`
(ADR-0015). Field Encryption is checked for shape only; no key material is
resolved or read.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from filedge.authoring import AuthoringSession
from filedge.config import PipelineConfig

# The six Validation Scope dimensions, used to tag each finding so an Authoring
# UI can group feedback without parsing free text.
SCOPE_CONFIG_LOADING = "config_loading"
SCOPE_PARSER = "parser"
SCOPE_COLUMN_TOLERANCE = "column_tolerance"
SCOPE_STRICT_MODE = "strict_mode"
SCOPE_FIELD_ENCRYPTION = "field_encryption"
SCOPE_WRITE_MODE = "write_mode"


@dataclass
class ValidationFinding:
    """One piece of Authoring Validation feedback, tagged by Validation Scope.

    `row_number` and `column` carry row-level context for Strict Mode coercion
    failures so an Authoring UI can point the reviewer at the offending cell;
    they are `None` for File-wide findings.
    """

    scope: str
    ok: bool
    message: str
    row_number: Optional[int] = None
    column: Optional[str] = None


@dataclass
class AuthoringValidationReport:
    """The structured result of one Authoring Validation pass."""

    findings: List[ValidationFinding] = field(default_factory=list)
    rows_checked: int = 0

    @property
    def ok(self) -> bool:
        """True when nothing in the Validation Scope flagged an incompatibility."""
        return all(f.ok for f in self.findings)

    def findings_in(self, scope: str) -> List[ValidationFinding]:
        """Return the findings tagged with a single Validation Scope dimension."""
        return [f for f in self.findings if f.scope == scope]

    @property
    def failures(self) -> List[ValidationFinding]:
        """Every finding that flagged an incompatibility."""
        return [f for f in self.findings if not f.ok]


def validate_authoring(
    file: str,
    config: PipelineConfig,
    *,
    encoding: Optional[str] = None,
    sheet=None,
    sample_rows: Optional[int] = None,
) -> AuthoringValidationReport:
    """Validate a sample File against a Pipeline Config across the Validation Scope.

    `config` is an already-loaded `PipelineConfig`; a Pipeline Config Draft's
    `.to_config()` produces exactly one, which is itself the config-loading
    round-trip the Validation Scope promises. The returned report enumerates
    findings for config loading, Field Encryption shape, and Write Mode settings
    (none of which need the File), then — if the Parser can read the sample —
    Column Tolerance and Strict Mode findings derived from the actual rows.

    This function performs no ingestion, opens no Destination, and writes no
    Audit Record.
    """
    report = AuthoringValidationReport()

    # The caller handed us a constructed PipelineConfig, so config loading has
    # already succeeded; record it so the report enumerates the whole Scope.
    report.findings.append(
        ValidationFinding(
            scope=SCOPE_CONFIG_LOADING,
            ok=True,
            message="Pipeline Config loaded; round-trips through the config loader.",
        )
    )

    # File-independent structural checks first, so they still surface even when
    # the sample File itself is unreadable.
    report.findings.extend(_field_encryption_findings(config))
    report.findings.extend(_write_mode_findings(config))

    session = AuthoringSession(
        file, config.format, config=config, encoding=encoding, sheet=sheet
    )

    # Parser readability: a single 1-row preview both proves the Parser can read
    # the declared format and hands us the source columns present in the File.
    try:
        head = session.preview(num_rows=1)
    except Exception as e:  # noqa: BLE001 — surface any Parser failure as a finding
        report.findings.append(
            ValidationFinding(
                scope=SCOPE_PARSER,
                ok=False,
                message=f"Parser could not read the sample File: {e}",
            )
        )
        return report

    report.findings.append(
        ValidationFinding(
            scope=SCOPE_PARSER,
            ok=True,
            message=f"Parser read the sample File as format {config.format!r}.",
        )
    )

    report.findings.extend(_column_tolerance_findings(config, head))

    # Strict Mode: run the same validation the loader uses, turning each rejected
    # row into a finding carrying row-level context.
    result = session.validate(sample_rows=sample_rows)
    report.rows_checked = result.rows_checked
    if result.failures:
        for rf in result.failures:
            report.findings.append(
                ValidationFinding(
                    scope=SCOPE_STRICT_MODE,
                    ok=False,
                    message=rf.error,
                    row_number=rf.row_number,
                    column=rf.column,
                )
            )
    else:
        report.findings.append(
            ValidationFinding(
                scope=SCOPE_STRICT_MODE,
                ok=True,
                message=(
                    f"All {result.rows_checked} sampled row(s) pass Strict Mode "
                    "type coercion."
                ),
            )
        )

    return report


def _field_encryption_findings(config: PipelineConfig) -> List[ValidationFinding]:
    """Report Field Encryption shape, without resolving any key material.

    The Pipeline Config loader has already rejected malformed `encrypt:`/`hash:`
    blocks (wrong algorithm, non-`string` encrypt column, bad key reference), so
    a constructed `PipelineConfig` carries only structurally valid declarations.
    We enumerate which destination columns they target as read-only evidence; we
    never read the referenced key.
    """
    targets = [
        c.dest
        for c in config.columns
        if c.encrypt is not None or c.hash is not None
    ]
    if not targets:
        return [
            ValidationFinding(
                scope=SCOPE_FIELD_ENCRYPTION,
                ok=True,
                message="No Field Encryption declared.",
            )
        ]
    return [
        ValidationFinding(
            scope=SCOPE_FIELD_ENCRYPTION,
            ok=True,
            message=(
                "Field Encryption declarations are structurally valid for "
                f"{len(targets)} column(s): {', '.join(targets)} "
                "(key material not read)."
            ),
        )
    ]


def _write_mode_findings(config: PipelineConfig) -> List[ValidationFinding]:
    """Report whether the Write Mode has the settings it requires.

    `write_mode: cdc` needs a `cdc:` block declaring a business key, a sequence
    column, and an operation column, all referencing declared source columns. A
    Pipeline Config Draft that skipped those settings is reported here as a
    failure rather than discovered only at ingestion time.
    """
    if config.write_mode != "cdc":
        return [
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=True,
                message=f"Write Mode {config.write_mode!r} needs no extra settings.",
            )
        ]

    if config.cdc is None:
        return [
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=False,
                message="Write Mode 'cdc' requires a cdc: block.",
            )
        ]

    findings: List[ValidationFinding] = []
    declared = {c.source for c in config.columns}

    if not config.cdc.keys:
        findings.append(
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=False,
                message="Write Mode 'cdc' requires at least one business key column.",
            )
        )
    for key in config.cdc.keys:
        if key not in declared:
            findings.append(
                ValidationFinding(
                    scope=SCOPE_WRITE_MODE,
                    ok=False,
                    message=f"CDC business key {key!r} must be a declared column.",
                )
            )

    if not config.cdc.sequence_by:
        findings.append(
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=False,
                message="Write Mode 'cdc' requires a sequence column.",
            )
        )
    elif config.cdc.sequence_by not in declared:
        findings.append(
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=False,
                message=(
                    f"CDC sequence column {config.cdc.sequence_by!r} must be a "
                    "declared column."
                ),
            )
        )

    if not config.cdc.operation_column:
        findings.append(
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=False,
                message="Write Mode 'cdc' requires an operation column.",
            )
        )

    if not findings:
        findings.append(
            ValidationFinding(
                scope=SCOPE_WRITE_MODE,
                ok=True,
                message="Write Mode 'cdc' has its required business key and sequence settings.",
            )
        )
    return findings


def _column_tolerance_findings(
    config: PipelineConfig, head: List[dict]
) -> List[ValidationFinding]:
    """Apply the Column Tolerance asymmetry to the File's actual source columns.

    Extra source columns are tolerated (reported as an informational ok finding);
    required columns absent from the source are reported as failures. When the
    sample has no rows we cannot see its source columns, so we say so rather than
    guess.
    """
    if not head:
        return [
            ValidationFinding(
                scope=SCOPE_COLUMN_TOLERANCE,
                ok=True,
                message="Sample File has no rows; source columns not inspected.",
            )
        ]

    present = set(head[0].keys())
    declared = {c.source for c in config.columns}
    findings: List[ValidationFinding] = []

    missing_required = [
        c.source for c in config.columns if c.required and c.source not in present
    ]
    for source in missing_required:
        findings.append(
            ValidationFinding(
                scope=SCOPE_COLUMN_TOLERANCE,
                ok=False,
                message=f"Required column {source!r} is missing from the sample File.",
                column=source,
            )
        )

    extra = sorted(present - declared)
    if extra:
        findings.append(
            ValidationFinding(
                scope=SCOPE_COLUMN_TOLERANCE,
                ok=True,
                message=(
                    "Extra source column(s) tolerated and ignored: "
                    f"{', '.join(extra)}."
                ),
            )
        )

    if not missing_required:
        findings.append(
            ValidationFinding(
                scope=SCOPE_COLUMN_TOLERANCE,
                ok=True,
                message="All required columns are present in the sample File.",
            )
        )
    return findings
