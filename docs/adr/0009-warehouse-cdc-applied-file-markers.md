# ADR-0009: Warehouse CDC uses Destination-side Applied File Markers

**Status:** Accepted

## Context

CDC Files are applied as SCD Type 1 changes: inserts and updates replace the current row for a business key, and deletes remove the current row. For transactional destinations such as SQLite and PostgreSQL, the Connector can apply those changes in a database transaction.

Warehouse destinations such as BigQuery and Databricks have a different retry-safety problem. Replaying the same CDC File is not the same as re-inserting rows with the same `_source_file_hash`: it re-applies business-key mutations. The existing Audit DB records whether a File reached COMMITTED, but ADR-0004 leaves a failure window where the Destination write can succeed and the Audit DB update can fail. A retry after stale-lock reclaim must therefore be safe even if the Audit DB does not yet know the File's destination effects already happened.

## Decision

Warehouse CDC Connectors must use a Destination-side Applied File Marker keyed conceptually by destination table and Content Hash.

For a CDC File, the Connector applies the target-table changes and records the Applied File Marker as one destination-side unit of work where the warehouse supports that. The marker is written only after the target-table effects are complete. On retry, if the marker already exists for the destination table and Content Hash, the Connector treats the File as already applied and returns successfully.

The Applied File Marker complements the Audit DB; it does not replace the Audit Record. The Audit DB remains the control plane for Run state, retries, and operator visibility.

## Consequences

- Warehouse CDC retry safety does not depend on BigQuery job metadata retention or Databricks load idempotency alone.
- The same CDC File may be applied to different destination tables because the marker is scoped to the destination table as well as Content Hash.
- CDC Connectors need a warehouse-specific staging and set-based apply path. For BigQuery and Databricks, this means staging the transformed CDC rows, applying SCD Type 1 changes with warehouse DML such as `MERGE`, and then recording the Applied File Marker.
- Destination tables used with `write_mode: cdc` must support mutation. If a Destination table cannot support the required update/delete/merge behavior, the Connector should fail clearly rather than fall back to append-only CDC event storage.

## Alternatives Considered

**Rely only on the Audit DB.** Simpler, but unsafe across the ADR-0004 two-phase failure window. A Destination apply can succeed while the Audit DB still says PROCESSING.

**Rely on `_source_file_hash` in destination rows.** Works for append-style idempotency, but not for CDC deletes or updates where the current row for a business key may be removed or replaced.

**Rely on warehouse load job IDs.** BigQuery job metadata retention is limited, and job identity does not by itself prove that the business-key mutation and any marker were applied as one unit.

**Write the Applied File Marker before applying target changes.** Rejected because a crash after the marker but before the target mutation would cause retry to skip an unapplied File.
