"""Emit the OpenLineage-shaped Source Manifest sidecar for a produced File.

This is the inverse of `filedge.source_manifest.discover_and_parse`: it writes a
`<data-file>.manifest.json` sidecar in the RunEvent shape that reader consumes,
so an API-sourced File carries the same audit-grade provenance as any file drop
(ADR-0011). The contract is a round-trip — anything this emits, the reader parses
into a valid `SourceMetadata`.

The emitter is a reusable building block: any Python Fetcher can call it to
produce a conformant sidecar without copying the reference client. It does not
emit OpenLineage *events* (no receiver, no transport) — only the sidecar shape.
"""

import json
from typing import Optional

from filedge.source_manifest import (
    DEFAULT_MANIFEST_VERSION,
    SIDECAR_SUFFIX,
)


def build_manifest(
    *,
    source_type: str,
    source_name: str,
    producer: str,
    run_id: str,
    output_name: str,
    started_at: str,
    finished_at: str,
    record_count: int,
    source_range: Optional[dict] = None,
) -> dict:
    """Build the OpenLineage RunEvent dict for a produced File.

    `job.namespace`/`job.name` carry source_type/source_name (the reader's
    required fields); the `_filedgeManifest` run facet carries version, timing,
    and record count; an `inputs[]._sourceRange` facet carries the cursor window.
    """
    run_facets = {
        "_filedgeManifest": {
            "manifest_version": DEFAULT_MANIFEST_VERSION,
            "started_at": started_at,
            "record_count": record_count,
        }
    }
    inputs = []
    if source_range is not None:
        inputs.append({
            "namespace": source_type,
            "name": source_name,
            "facets": {"_sourceRange": source_range},
        })
    return {
        "eventType": "COMPLETE",
        "eventTime": finished_at,
        "producer": producer,
        "run": {"runId": run_id, "facets": run_facets},
        "job": {"namespace": source_type, "name": source_name},
        "inputs": inputs,
        "outputs": [{"namespace": "filedge", "name": output_name}],
    }


def emit_manifest(
    data_file_path: str,
    *,
    source_type: str,
    source_name: str,
    producer: str,
    run_id: str,
    started_at: str,
    finished_at: str,
    record_count: int,
    source_range: Optional[dict] = None,
) -> str:
    """Write the sidecar next to `data_file_path`; return the sidecar path."""
    import os

    manifest = build_manifest(
        source_type=source_type,
        source_name=source_name,
        producer=producer,
        run_id=run_id,
        output_name=os.path.basename(data_file_path),
        started_at=started_at,
        finished_at=finished_at,
        record_count=record_count,
        source_range=source_range,
    )
    sidecar_path = data_file_path + SIDECAR_SUFFIX
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return sidecar_path
