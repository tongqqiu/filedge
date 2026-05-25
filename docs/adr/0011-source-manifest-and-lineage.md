# ADR-0011: Source Manifest is an OpenLineage-Shaped Sidecar, Not an Event Stream

**Status:** Accepted

## Context

Filedge's ingestion boundary is the **File**: complete bytes in a Watched Directory, identified by Content Hash, processed through the audit state machine. ADR-0005 keeps SFTP transfer mechanics out of scope, ADR-0006 keeps API fetching out of scope, and ADR-0007 keeps queue consumption out of scope. External Fetchers, sync jobs, and Queue Materializers own those concerns and land complete Files for Filedge to ingest.

That boundary has been good for reliability and product clarity, but it leaves a visibility gap. An auditor or operator can trace a destination row back to a File through `_source_file_hash`, but when that File was produced by an external source tool, Filedge has no first-class way to answer upstream questions: which API cursor range produced this File? Which Kafka topic/partition/offset range was materialized? Which SFTP partner delivery, or which external job run? Without that linkage, teams pressure Filedge to expand into native API/SFTP/Kafka ingestion solely to get one visibility surface — re-opening exactly the boundaries ADR-0005/0006/0007 closed.

Two architectural questions had to be settled before any code lands:

1. **Vocabulary**: invent a Filedge-native manifest schema, or adopt an existing standard?
2. **Transport**: receive upstream provenance as **events** (HTTP/Kafka push to a Filedge endpoint) or read it as **co-located bytes** (a sidecar file next to the data File)?

## Decision

Source provenance is captured as an **OpenLineage-shaped JSON sidecar** placed adjacent to the data File in the Watched Directory. Filedge reads the sidecar when it discovers the File and stores the normalized common fields plus the full raw payload on the File's Audit Record, keyed by Content Hash. Filedge does **not** run an OpenLineage event receiver and does **not** integrate with a Marquez-style backend.

### Vocabulary: OpenLineage-shaped

The sidecar JSON conforms to the OpenLineage `RunEvent` shape where applicable: `eventType: COMPLETE`, `run.runId` as the external run identifier, `job.namespace` + `job.name` identifying the upstream tool and source, `inputs[]` describing source range coverage, `outputs[]` referencing the produced File, and `facets` carrying source-specific details (Kafka offset range, API cursor window, SFTP partner + remote path, vendor export job ID). Producers already emitting OpenLineage can dump the same RunEvent JSON they emit elsewhere. Producers that are not OpenLineage-native (rclone, custom scripts) fill only the small subset Filedge requires.

The schema is branded a **Filedge source manifest** that **follows OpenLineage facets where applicable** — not "Filedge supports OpenLineage." This sets correct expectations: Filedge reads the shape, but does not claim conformance to OpenLineage's event transport, lifecycle, or backend behavior.

### Transport: co-located sidecar, not events

The sidecar is a JSON file next to the data File with a predictable suffix: `<data-file>.manifest.json` (e.g. `2026-05-25-stripe-charges.ndjson` → `2026-05-25-stripe-charges.ndjson.manifest.json`). Manifest discovery does not open or parse the data File. Filedge reads the sidecar (if any) during the same pipeline registration step that hashes the data File.

Filedge does **not** receive OpenLineage events over HTTP, Kafka, or any push protocol. It does not poll a Marquez backend. It does not asynchronously correlate events with Files.

### Manifest policy

`pipeline.yaml` gains a `source_manifest:` policy with three modes:

- `disabled` — the parser is not invoked; no source metadata is attached
- `optional` (default) — the parser is invoked; valid manifests are recorded; missing or invalid manifests warn but do not fail the File
- `required` — the parser is invoked; missing or invalid manifests fail the File **before destination write**, with the error category and manifest path captured on the Audit Record

### Validation error taxonomy

The parser distinguishes:

- `manifest_missing` — no sidecar at the expected path
- `manifest_malformed_json` — sidecar exists but is not valid JSON
- `manifest_unsupported_version` — `manifest_version` is present but Filedge does not support it
- `manifest_missing_required_field` — required common fields absent (e.g. `source_type`, `source_name`)
- `manifest_invalid_source_range` — source-range shape is structurally invalid for the declared source type

Every error carries the File identity (filename, Content Hash) and the manifest path so upstream tool owners can locate and repair the right artifact.

### Common queryable fields

These fields are extracted from the manifest and stored in dedicated Audit DB columns so dashboards and `filedge lineage` queries do not need to parse JSON:

| Field | Source from OpenLineage RunEvent | Purpose |
| --- | --- | --- |
| `manifest_version` | Filedge facet | Stable schema evolution |
| `source_type` | `job.namespace` (e.g. `api`, `queue`, `sftp`, `vendor_export`, `file_drop`) | Distinguish source kinds |
| `source_name` | `job.name` (e.g. `stripe.charges`, `kafka.orders`, `acme-partner`) | Identify the specific producer |
| `producer` | `producer` URI (e.g. `https://github.com/dlt-hub/dlt`) | Tool that emitted the manifest |
| `external_run_id` | `run.runId` | Correlate with upstream scheduler |
| `started_at` | `eventTime` of the START event, or facet | Source materialization start |
| `finished_at` | `eventTime` of the COMPLETE event | Source materialization end |
| `record_count` | `outputFacets.recordCount` (custom facet) | Producer-claimed row count |

The full raw manifest payload is also stored verbatim so source-specific facets (Kafka offset ranges, API cursor windows, SFTP partner paths, vendor export job IDs, arbitrary namespaced extras) are not lost.

### What this decision reinforces

- **ADR-0005**: SFTP transfer mechanics stay out of scope. The sidecar is provenance metadata, not a transfer-completion protocol — the sync layer still owns partial-transfer detection and partner acknowledgement.
- **ADR-0006**: Filedge does not fetch from APIs. The sidecar describes a fetch that already happened; the Fetcher still owns auth, pagination, and incremental cursor management.
- **ADR-0007**: Filedge does not consume queues. The sidecar describes a materialization that already happened; the Queue Materializer still owns consumer groups, offset commits, rebalances, and poison-message handling.

The product message is unchanged: bring your Fetcher, sync job, or Queue Materializer; Filedge provides the uniform audit spine from materialized File to destination rows. The sidecar makes that audit spine end-to-end.

## Consequences

- **Producers reuse existing vocabulary.** Tools already emitting OpenLineage (Airflow, dbt, Flink, Spark, Great Expectations, partial dlt, Airbyte plugin, Kafka Connect via Marquez plugin) can dump their RunEvent JSON next to the File. No new schema for them to learn.
- **Producers that are not OpenLineage-native are not blocked.** rclone scripts and custom jobs fill only the required common fields. The schema's optionality is the on-ramp.
- **Filedge does not run a server.** No HTTP listener, no Marquez integration, no event ordering or late-event reconciliation. The batch deploy model from ADR-0010 is preserved.
- **Idempotency is unchanged.** Source metadata annotates a File; it never becomes the deduplication key. Content Hash remains the only idempotency key, per ADR-0002.
- **Audit DB migrations stay backward compatible.** Existing Audit Records without source metadata remain readable. Direct file drops continue to work unchanged (optional mode default).
- **Required mode gives regulated pipelines a hard guarantee.** A pipeline configured with `source_manifest: required` cannot silently lose audit coverage — a missing or invalid manifest fails the File before destination write.
- **Naming risk: "OpenLineage support" overpromise.** Calling the sidecar an "OpenLineage manifest" would imply event receiver support that Filedge does not provide. The chosen framing — "Filedge source manifest, OpenLineage-shaped" — is deliberate.
- **Future expansion is constrained, not blocked.** If the ecosystem moves and event ingestion becomes the dominant pattern, Filedge can add an event listener as a separate component without invalidating the sidecar contract. Sidecar-first does not foreclose event-later.

## Alternatives Considered

**Filedge as an OpenLineage event receiver.** Run an HTTP listener or pull from a Marquez backend, then asynchronously correlate received events with Files at ingestion time. Rejected because it (a) re-opens the network handoff ADR-0005/0006/0007 closed, (b) introduces event ordering and late-event reconciliation Filedge would have to own, (c) breaks the batch deploy model — Filedge becomes a long-running process or grows a dependency on a backend, and (d) does not work for producers that do not emit OpenLineage.

**Invent a Filedge-native manifest schema.** Smaller surface area, no OpenLineage dependency. Rejected because it ignores existing producer adoption (Airflow, dbt, Flink, Spark, Great Expectations, dlt, Airbyte, Kafka Connect) and forces those producers into a second vocabulary. Filedge's audit spine story is stronger when it leans on an industry schema than when it invents one.

**Manifest embedded inside the data File** (NDJSON header line, Parquet file-level metadata). Rejected because (a) it requires Filedge or the parser to open and parse the data File to discover provenance, coupling provenance discovery to format-specific parser code, (b) it makes one File two things, and (c) Parquet metadata limits and NDJSON-leading-line conventions are not portable across producers.

**Manifest stored in object metadata** (S3/GCS custom metadata). Rejected for portability — Watched Directory may be local disk, mounted bucket, or filesystem abstraction. A sidecar JSON file works uniformly across local, GCS, and S3 backends via the existing fsspec layer.

**Manifest pulled from an external registry by Content Hash.** Filedge queries a backend (Marquez, a custom service) using the just-computed Content Hash to fetch provenance after registration. Rejected because (a) it adds a network dependency at ingestion time, (b) the upstream tool would still need to write the manifest somewhere — picking "next to the data File" is simpler than "publish to a registry Filedge can read."
