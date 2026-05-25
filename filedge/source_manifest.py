"""Source manifest parser.

Reads an OpenLineage-shaped JSON sidecar placed next to a data File and
extracts the common fields Filedge persists on the File's Audit Record.
See ADR-0011 for the architectural rationale.
"""
import json
from dataclasses import dataclass
from typing import Optional


SIDECAR_SUFFIX = ".manifest.json"


@dataclass(frozen=True)
class SourceMetadata:
    source_type: str
    source_name: str
    producer: Optional[str]
    external_run_id: Optional[str]
    raw_payload: str


@dataclass(frozen=True)
class ManifestResult:
    found: bool
    metadata: Optional[SourceMetadata]


def discover_and_parse(data_file_path: str, fs=None) -> ManifestResult:
    """Look for `<data_file>.manifest.json` and parse it as an OpenLineage RunEvent."""
    manifest_path = data_file_path + SIDECAR_SUFFIX
    raw = _read_text(manifest_path, fs)
    if raw is None:
        return ManifestResult(found=False, metadata=None)

    payload = json.loads(raw)
    job = payload.get("job", {})
    run = payload.get("run", {})

    metadata = SourceMetadata(
        source_type=job.get("namespace", ""),
        source_name=job.get("name", ""),
        producer=payload.get("producer"),
        external_run_id=run.get("runId"),
        raw_payload=raw,
    )
    return ManifestResult(found=True, metadata=metadata)


def _read_text(path: str, fs) -> Optional[str]:
    try:
        if fs is None:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        with fs.open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
