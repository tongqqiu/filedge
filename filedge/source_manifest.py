"""Source manifest parser.

Reads an OpenLineage-shaped JSON sidecar placed next to a data File and
extracts the common fields Filedge persists on the File's Audit Record.
See ADR-0011 for the architectural rationale.

Validation errors are returned as typed categories rather than raised so
the pipeline can apply the configured `source_manifest:` policy
(disabled / optional / required) without try/except control flow.
"""
import json
from dataclasses import dataclass
from typing import Optional


SIDECAR_SUFFIX = ".manifest.json"
SUPPORTED_MANIFEST_VERSIONS = {"1"}
DEFAULT_MANIFEST_VERSION = "1"

ERROR_MISSING = "manifest_missing"
ERROR_MALFORMED_JSON = "manifest_malformed_json"
ERROR_UNSUPPORTED_VERSION = "manifest_unsupported_version"
ERROR_MISSING_REQUIRED_FIELD = "manifest_missing_required_field"
ERROR_INVALID_SOURCE_RANGE = "manifest_invalid_source_range"


@dataclass(frozen=True)
class SourceMetadata:
    source_type: str
    source_name: str
    producer: Optional[str]
    external_run_id: Optional[str]
    raw_payload: str


@dataclass(frozen=True)
class ManifestResult:
    """Outcome of looking for and parsing a sidecar manifest.

    `found` is True when a sidecar file existed at the expected path,
    regardless of whether parsing/validation succeeded — that distinguishes
    `manifest_missing` from other error categories.
    """
    found: bool
    metadata: Optional[SourceMetadata]
    error_category: Optional[str]
    manifest_path: str


def discover_and_parse(data_file_path: str, fs=None) -> ManifestResult:
    """Look for `<data_file>.manifest.json` and parse it as an OpenLineage RunEvent.

    Returns a `ManifestResult`. Inspect `error_category` to determine the
    failure mode. The caller (pipeline) decides whether the error is fatal
    based on the pipeline's `source_manifest:` policy.
    """
    manifest_path = data_file_path + SIDECAR_SUFFIX
    raw = _read_text(manifest_path, fs)
    if raw is None:
        return ManifestResult(
            found=False, metadata=None,
            error_category=ERROR_MISSING, manifest_path=manifest_path,
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ManifestResult(
            found=True, metadata=None,
            error_category=ERROR_MALFORMED_JSON, manifest_path=manifest_path,
        )

    version = _extract_version(payload)
    if version not in SUPPORTED_MANIFEST_VERSIONS:
        return ManifestResult(
            found=True, metadata=None,
            error_category=ERROR_UNSUPPORTED_VERSION, manifest_path=manifest_path,
        )

    job = payload.get("job") or {}
    source_type = job.get("namespace")
    source_name = job.get("name")
    if not source_type or not source_name:
        return ManifestResult(
            found=True, metadata=None,
            error_category=ERROR_MISSING_REQUIRED_FIELD, manifest_path=manifest_path,
        )

    if not _source_range_is_valid(payload):
        return ManifestResult(
            found=True, metadata=None,
            error_category=ERROR_INVALID_SOURCE_RANGE, manifest_path=manifest_path,
        )

    run = payload.get("run") or {}
    metadata = SourceMetadata(
        source_type=source_type,
        source_name=source_name,
        producer=payload.get("producer"),
        external_run_id=run.get("runId"),
        raw_payload=raw,
    )
    return ManifestResult(
        found=True, metadata=metadata,
        error_category=None, manifest_path=manifest_path,
    )


def _extract_version(payload: dict) -> str:
    run = payload.get("run") or {}
    facets = run.get("facets") or {}
    fm = facets.get("_filedgeManifest") or {}
    return fm.get("manifest_version", DEFAULT_MANIFEST_VERSION)


def _source_range_is_valid(payload: dict) -> bool:
    """A source-range facet may attach to any input. When present it must be an object."""
    for inp in payload.get("inputs") or []:
        facets = inp.get("facets") or {}
        if "_sourceRange" in facets and not isinstance(facets["_sourceRange"], dict):
            return False
    return True


def _read_text(path: str, fs) -> Optional[str]:
    try:
        if fs is None:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        with fs.open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
