"""The Pipeline Folder writer and Authoring Runbook renderer (CONTEXT.md:
Pipeline Folder, Authoring Runbook; ADR-0017).

`write_pipeline_folder` persists a validated Pipeline Config Draft to disk as a
Pipeline Folder — `pipelines/<id>/{pipeline.yaml, RUNBOOK.md}` under the
workspace root — and records the Pipeline in the Pipeline Registry, creating the
Registry lazily on the first Pipeline. The `pipeline.yaml` is the exact artifact
the Operator CLI already consumes, written from the draft's config mapping and
proven to round-trip through the config loader before anything lands on disk.

The Authoring Runbook is a non-secret Markdown note: it references the sample
File by path (never copies it), names the Audit DB connection placeholder
(never its resolved value), and suggests the `filedge validate` / `healthcheck`
/ `run` / `export-audit` commands. No environment variable is ever read, so no
secret can bleed into an authored artifact.

This writer stays non-secret: Connector settings may be written to
`pipeline.yaml`, but Credential Placeholders only name runtime environment
variables. Secret values are never read or exported.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

import yaml

from filedge.config import config_from_dict
from filedge.pipeline_registry import RegistryEntry, add_entry, registry_path

PIPELINES_DIRNAME = "pipelines"
CONFIG_FILENAME = "pipeline.yaml"
RUNBOOK_FILENAME = "RUNBOOK.md"


@dataclass
class PipelineFolderResult:
    """Where a freshly authored Pipeline's artifacts landed."""

    pipeline_id: str
    folder: str  # workspace-relative path to the Pipeline Folder
    config_path: str  # absolute path to pipeline.yaml
    runbook_path: str  # absolute path to RUNBOOK.md
    registry_path: str  # absolute path to pipeline-registry.yaml
    watched_directory: str  # Registry-recorded Watched Directory
    audit_db: str  # Audit DB connection placeholder (never a literal value)
    audit_export: str  # Registry-recorded Audit Export destination


def operator_handoff_commands(
    *,
    sample_file: str,
    config_path: str,
    watched_directory: str,
    audit_db: str,
    audit_export: str,
) -> list[str]:
    """Render the post-generation Operator CLI handoff command list.

    The single source of truth for the validate / healthcheck / run /
    export-audit handoff. Both the Authoring Runbook and the Authoring UI's
    `suggested_commands` render this list, so the command set and the Audit DB
    shell-reference rule cannot drift between them. `audit_db` is a placeholder
    (``env:NAME`` / ``secrets:/abs/path``); its resolved value is never read.
    """
    audit_ref = _audit_db_shell_ref(audit_db)
    return [
        f"filedge validate {sample_file} --config {config_path}",
        f"filedge healthcheck --config {config_path} --audit-db-url {audit_ref}",
        f"filedge run --dir {watched_directory} --config {config_path} "
        f"--audit-db-url {audit_ref}",
        f"filedge export-audit --audit-db-url {audit_ref} "
        f"--output {audit_export}/index.html",
    ]


def slugify_pipeline_id(name: str) -> str:
    """Derive a Pipeline id slug: lowercase, non-alphanumeric runs → single `-`."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot derive a Pipeline id from {name!r}.")
    return slug


def write_pipeline_folder(
    workspace: str,
    config: dict,
    *,
    sample_file: str,
    out: Optional[str] = None,
    watched_directory: Optional[str] = None,
    audit_db: Optional[str] = None,
    audit_export: Optional[str] = None,
    confidence_acknowledgements: Optional[list[dict]] = None,
    credential_placeholders: Optional[list[dict]] = None,
    field_encryption_columns: Optional[list[dict]] = None,
) -> PipelineFolderResult:
    """Write a Pipeline Folder for `config` and register it in the workspace.

    `config` is a Pipeline Config mapping (a Pipeline Config Draft's
    `to_config_dict()`). The id is slugged from `out` if given, else from the
    config's `dest_table`. The Registry references (Watched Directory, Audit DB
    placeholder, Audit Export) default to per-id paths and may be overridden;
    `audit_db` is always a placeholder, never a literal connection string.
    """
    # Prove the generated pipeline.yaml round-trips through the config loader
    # before writing anything; an invalid draft fails here, not at ingestion.
    config_from_dict(config)

    pipeline_id = slugify_pipeline_id(out if out is not None else config["dest_table"])
    folder_rel = f"{PIPELINES_DIRNAME}/{pipeline_id}"
    folder_abs = os.path.join(workspace, PIPELINES_DIRNAME, pipeline_id)
    if os.path.exists(folder_abs):
        raise ValueError(
            f"Pipeline Folder for id {pipeline_id!r} already exists at {folder_abs!r}."
        )

    audit_db = audit_db if audit_db is not None else _default_audit_db(pipeline_id)
    watched_directory = (
        watched_directory if watched_directory is not None else f"./landing/{pipeline_id}"
    )
    audit_export = (
        audit_export if audit_export is not None else f"./audit-exports/{pipeline_id}"
    )

    os.makedirs(folder_abs)
    config_path = os.path.join(folder_abs, CONFIG_FILENAME)
    runbook_path = os.path.join(folder_abs, RUNBOOK_FILENAME)

    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)

    runbook = _render_runbook(
        pipeline_id=pipeline_id,
        config=config,
        sample_file=sample_file,
        folder_rel=folder_rel,
        watched_directory=watched_directory,
        audit_db=audit_db,
        audit_export=audit_export,
        confidence_acknowledgements=confidence_acknowledgements or [],
        credential_placeholders=credential_placeholders or [],
        field_encryption_columns=field_encryption_columns or [],
    )
    with open(runbook_path, "w") as f:
        f.write(runbook)

    add_entry(
        workspace,
        RegistryEntry(
            id=pipeline_id,
            folder=folder_rel,
            watched_directory=watched_directory,
            audit_db=audit_db,
            audit_export=audit_export,
        ),
    )

    return PipelineFolderResult(
        pipeline_id=pipeline_id,
        folder=folder_rel,
        config_path=config_path,
        runbook_path=runbook_path,
        registry_path=registry_path(workspace),
        watched_directory=watched_directory,
        audit_db=audit_db,
        audit_export=audit_export,
    )


def _default_audit_db(pipeline_id: str) -> str:
    env_name = re.sub(r"[^A-Z0-9]+", "_", pipeline_id.upper()) + "_AUDIT_DB_URL"
    return f"env:{env_name}"


def _audit_db_shell_ref(audit_db: str) -> str:
    """Render an Audit DB placeholder as a shell reference, never a value."""
    if audit_db.startswith("env:"):
        return f'"${audit_db[len("env:"):]}"'
    # secrets:/path — show the placeholder itself; the Operator resolves it.
    return f"<{audit_db}>"


def _render_runbook(
    *,
    pipeline_id: str,
    config: dict,
    sample_file: str,
    folder_rel: str,
    watched_directory: str,
    audit_db: str,
    audit_export: str,
    confidence_acknowledgements: list[dict],
    credential_placeholders: list[dict],
    field_encryption_columns: list[dict],
) -> str:
    """Render the non-secret Authoring Runbook Markdown for one Pipeline."""
    config_rel = f"{folder_rel}/{CONFIG_FILENAME}"
    commands = "\n".join(
        operator_handoff_commands(
            sample_file=sample_file,
            config_path=config_rel,
            watched_directory=watched_directory,
            audit_db=audit_db,
            audit_export=audit_export,
        )
    )
    confidence_section = _render_confidence_acknowledgements(
        confidence_acknowledgements
    )
    credential_section = _render_credential_placeholders(credential_placeholders)
    field_encryption_section = _render_field_encryption_columns(
        field_encryption_columns
    )
    return f"""# Authoring Runbook: {pipeline_id}

A non-secret companion note produced during Pipeline Authoring. It records how
this Pipeline was authored and how to operate it. It does not schedule, run, or
deploy the Pipeline, and it contains no secret values.

## Sample File

Authored from sample File: `{sample_file}`

The sample File is referenced by path only; it is never copied into the Pipeline
Folder, so this folder accumulates no source data or PII.

## Pipeline

- Destination table: `{config.get("dest_table", "")}`
- Format: `{config.get("format", "")}`
- Write Mode: `{config.get("write_mode", "append")}`
- Pipeline Config: `{config_rel}`
- Watched Directory: `{watched_directory}`
- Audit DB connection placeholder: `{audit_db}` (resolved from the environment at
  run time; the literal connection string is never stored here)
- Audit Export destination: `{audit_export}`

## Accepted Confidence Tiers

{confidence_section}

## Credential Placeholders

{credential_section}

## Field Encryption

{field_encryption_section}

## Validation Scope assumptions

Authoring Validation covered Parser readability, Column Tolerance, Strict Mode,
Field Encryption shape, Write Mode settings, and config loading. It did not check
Destination reachability, production credentials, or destination table readiness
— run `filedge healthcheck` for those.

## Suggested next commands

```sh
{commands}
```
"""


def _render_confidence_acknowledgements(acknowledgements: list[dict]) -> str:
    if not acknowledgements:
        return "No low or ambiguous Confidence Tier acknowledgements recorded."
    lines = []
    for item in acknowledgements:
        lines.append(
            "- "
            f"Source `{item.get('source', '')}` -> destination `{item.get('dest', '')}`: "
            f"accepted `{item.get('confidence', '')}` Confidence Tier. "
            f"Evidence: {item.get('evidence', '')}"
        )
    return "\n".join(lines)


def _render_field_encryption_columns(columns: list[dict]) -> str:
    """Render declared Field Encryption columns; key material is never read.

    Each `key` value is a Credential Placeholder reference (``env:NAME`` /
    ``secrets:/abs/path``), not the resolved secret, so the Runbook stays
    non-secret by construction.
    """
    if not columns:
        return "No Field Encryption columns declared."
    lines = []
    for item in columns:
        source = item.get("source", "")
        dest = item.get("dest", "")
        parts = [f"- Source `{source}` -> destination `{dest}`"]
        encrypt = item.get("encrypt")
        if encrypt:
            parts.append(
                f"encrypt `{encrypt.get('algorithm', '')}` "
                f"using key reference `{encrypt.get('key', '')}` "
                "(key material not read)"
            )
        hash_block = item.get("hash")
        if hash_block:
            parts.append(
                f"hash `{hash_block.get('algorithm', '')}` "
                f"using key reference `{hash_block.get('key', '')}` "
                "(key material not read)"
            )
        lines.append("; ".join(parts))
    return "\n".join(lines)


def _render_credential_placeholders(placeholders: list[dict]) -> str:
    if not placeholders:
        return "No Connector Credential Placeholders recorded."
    lines = []
    for item in placeholders:
        lines.append(
            "- "
            f"`{item.get('env_var', '')}`: {item.get('purpose', '')}"
        )
    return "\n".join(lines)
