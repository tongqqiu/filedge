"""Shared building blocks for external source companions.

Filedge's ingestion boundary is the File (ADR-0006/0007): external Fetchers and
Queue Materializers land complete Files in a Watched Directory, and `filedge
run` ingests them. Those companions share the same three reliability mechanics,
which live here so neither owns the other:

- ``manifest`` — emit the OpenLineage-shaped Source Manifest sidecar the core
  reader (`filedge.source_manifest`) already consumes (ADR-0011)
- ``promotion`` — the Fetch Lock (a per-source filesystem mutex) and the
  sidecar-then-data atomic promotion into the Watched Directory
- ``staging`` — write a complete NDJSON File into a staging area, window-tagged

The core ingestion path imports nothing from here; these are companion-only.
The Reference Fetcher (`filedge.fetch`, ADR-0018) and the Reference Queue
Materializer both build on this toolkit.
"""
