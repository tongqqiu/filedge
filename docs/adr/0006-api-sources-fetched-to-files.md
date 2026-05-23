# API Sources are materialized as files before ingestion

API data from SaaS tools (Stripe, Salesforce, HubSpot, etc.) is not loaded directly into the destination. Instead, a Fetcher (dlt, dlthub.com) pulls from the API, materializes the response as NDJSON files in the Watched Directory, and the standard `filedge run` pipeline ingests those files. dlt is used as a Fetcher, not a Loader.

The alternative — letting dlt write directly to the destination — is simpler to configure but produces a two-tier audit system: file drops get content-hash deduplication, strict atomic commits, and `filedge status` visibility; API pulls get dlt's internal state. For fintech operators, this inconsistency is unacceptable — an auditor asking "what data came in from Stripe on a given date and which destination rows did it produce?" must get the same answer format regardless of source type.

By materializing API responses as files first, every data source — file drop or API pull — passes through the same PENDING → COMMITTED state machine, carries the same `_source_file_hash` row-level provenance, and appears in `filedge status`. The Fetcher is responsible for ensuring only complete files land in the Watched Directory; partial fetches must not be visible to the pipeline.

## Considered Options

- **dlt direct-to-destination**: simpler dlt configuration, no file materialization step, but breaks audit uniformity across source types.
- **Build native API connectors**: full control, no dlt dependency, but connector coverage (pagination, auth, schema evolution per SaaS vendor) would require more effort than building the rest of this system combined.
