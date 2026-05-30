"""The Reference Fetcher — a first-party example of the external Fetcher role.

Filedge's ingestion boundary is the File (ADR-0006): API data must be
materialized as complete NDJSON Files in a Watched Directory by an *external*
Fetcher before `filedge run` ingests it. This subpackage is a runnable example
of that role, not part of the core ingestion path — `filedge.cli` imports
nothing from here, and the Fetcher is exposed through its own `filedge-fetch`
entry point. It is never a loader of record; `filedge run` still owns every
Destination Commit. See ADR-0018.

The pieces are small, independently testable modules:

- ``sources_config`` — parse/validate ``sources.yaml`` into a ``FetchPlan``
- ``cursor_state``  — persist and advance the incremental cursor
- ``source_client`` — page through an HTTP JSON API (rate-limit aware)
- ``staging_writer``— write a complete NDJSON File into a staging area
- ``manifest_emitter`` — emit the OpenLineage-shaped Source Manifest sidecar
- ``promotion``    — promote staged File + sidecar under a Fetch Lock
- ``orchestrator`` — wire it together; advance the cursor only after promotion
"""
