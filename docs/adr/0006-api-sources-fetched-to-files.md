# API Sources become Files before ingestion

Filedge does not fetch directly from SaaS APIs and does not load API responses directly into the destination. Its ingestion boundary is the **File**: complete bytes in a Watched Directory, identified by Content Hash and processed through the audit state machine.

API data from SaaS tools (Stripe, Salesforce, HubSpot, etc.) must first be materialized as complete NDJSON files by an upstream Fetcher. That Fetcher may be dlt, a vendor export job, a custom script, Airbyte, Meltano, or any other tool that can land complete files in the Watched Directory. Once the file exists, `filedge run` ingests it exactly like a file drop.

The rejected alternative is using an API tool as the loader of record. That may be simpler to configure, but it produces a two-tier audit system: file drops get Content Hash deduplication, strict atomic commits, row-level provenance, and `filedge status` visibility; API pulls get the API tool's own state and audit semantics. For fintech operators, that inconsistency is unacceptable. An auditor asking "what Stripe data landed on a given date and which destination rows did it produce?" must get the same answer format as they would for CSV or NDJSON file drops.

By requiring API responses to become Files first, every source passes through the same PENDING -> COMMITTED state machine, carries the same `_source_file_hash` row-level provenance, and appears in `filedge status`. The Fetcher is responsible for authentication, pagination, rate limits, incremental cursors, and ensuring only complete files land in the Watched Directory. Partial fetches must remain in staging or be deleted; they must not be visible to `filedge run`.

## Considered Options

- **External Fetcher writes files, Filedge ingests files**: keeps Filedge focused on the reliability, audit, validation, and destination Commit layer. API-specific behavior stays outside the core.
- **dlt direct-to-destination**: simpler dlt configuration, no file materialization step, but breaks audit uniformity across source types and makes dlt the loader of record.
- **Build native API connectors**: full control, no dlt dependency, but connector coverage (pagination, auth, schema evolution per SaaS vendor) would require more effort than building the rest of this system combined.
- **`filedge fetch` wrapper around dlt**: possible future convenience command, but not part of the core boundary. Building it too early would blur the product message and create an unnecessary hard dependency on dlt.
