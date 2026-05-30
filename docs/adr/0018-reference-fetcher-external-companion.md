# ADR-0018: The Reference Fetcher is an External Companion, Not Core

**Status:** Accepted

## Context

ADR-0006 fixed Filedge's ingestion boundary at the **File**: API data must be materialized as complete NDJSON Files in a Watched Directory by an *external* Fetcher before `filedge run` ingests it. ADR-0011 then built the Source Manifest sidecar so those external Fetchers can give a File audit-grade upstream provenance without Filedge running an event receiver. Both ADRs were deliberate boundary-keeping: they kept API/auth/pagination/cursor concerns *out* of the core so `filedge run` stays a reliability, audit, validation, and Commit layer.

But the upstream half of the architecture — the Fetcher, the API Source, the Sources Config, the Fetch Lock — existed only as glossary terms and prose. There was no runnable example of a *correct* Fetcher: one that stages a complete File, emits a valid Source Manifest, promotes only complete Files under a Fetch Lock, and manages an incremental cursor. Operators reinvented the contract from documentation, and the riskiest parts (never exposing a partial fetch; never landing a File without its provenance) were left to chance.

ADR-0006 explicitly considered a `filedge fetch` wrapper and deferred it: "possible future convenience command, but not part of the core boundary. Building it too early would blur the product message and create an unnecessary hard dependency on dlt." The open question this ADR settles: can Filedge ship a first-party Fetcher *example* without reopening that boundary?

## Decision

Filedge ships a **Reference Fetcher** that is an **external companion** to the ingestion path, not part of it and not a loader of record. The boundary of ADR-0005/0006/0007 is unchanged: `filedge run` remains the only thing that Commits to a Destination, and API behavior still lives outside the core.

Three properties make "external companion" concrete and enforceable:

1. **Separate entry point.** The Reference Fetcher is its own console script, `filedge-fetch`, not a `filedge` subcommand. The two are scheduled, scaled, and monitored independently — the same two-job pattern ADR-0005 recommends for SFTP sync (an rclone job lands files; a `filedge run` job ingests them).

2. **No core dependency.** The core ingestion path (`filedge.cli`, `filedge.pipeline`, and friends) imports nothing from the `filedge.fetch` subpackage. The reference source client uses only the Python standard library, so the companion adds no third-party runtime dependency. A `fetch` optional extra reserves the namespace for source-client dependencies a richer Fetcher might add later (e.g. a vendor SDK), mirroring the `authoring` extra.

3. **Same audit surface, via the existing sidecar.** The Fetcher emits the OpenLineage-shaped Source Manifest that `filedge.source_manifest` already reads (ADR-0011) — it does not invent a second provenance channel and does not emit OpenLineage *events*. An API-sourced File therefore carries the same `_source_file_hash` lineage and `filedge status` visibility as any file drop.

The reliability contract the Reference Fetcher demonstrates and enforces:

- **Stage, then promote.** Fetched records are written as one complete NDJSON File in a staging area, never directly into the Watched Directory. Promotion into the Watched Directory is a separate step guarded by a **Fetch Lock** (an atomically-created lock directory per API Source), so two concurrent fetches cannot race partial files into the landing zone.
- **Sidecar first, data File last.** During promotion the Source Manifest sidecar is moved before the data File (the thing `filedge run` discovers), and the data File is moved with an atomic rename. A File is therefore never visible without its provenance, and never visible half-written.
- **Cursor advances only after promotion.** The incremental cursor is persisted in the Fetcher's own state area and advanced only once the File is durably in the Watched Directory. A crash anywhere earlier retries the same window rather than skipping data.

The reference targets one open, no-auth HTTP JSON API so the example runs with zero credential setup. The source client sits behind a small seam, so a fintech API (Stripe, Plaid) is a new client implementation plus Sources Config, not a rewrite of the staging, promotion, manifest, or cursor logic.

## Considered Options

- **External companion `filedge-fetch` (chosen).** Proves the contract once, concretely, and gives operators a working template, while leaving ADR-0006's boundary and product message intact. The separate entry point and zero core dependency keep "companion, not core" structurally true rather than just documented.
- **`filedge fetch` core subcommand.** Simplest to discover, but folds fetching into the core command surface, pressures a dlt/HTTP dependency into core, and edges Filedge toward being a loader of record — exactly what ADR-0006 rejected.
- **Documentation only (no code).** Lowest risk to the boundary, but leaves the riskiest parts of the contract (partial-fetch invisibility, sidecar/data ordering, cursor-after-promotion) unproven and copy-pasted differently by every operator.
- **Bundle dlt as the reference Fetcher.** Real-world coverage of many SaaS APIs, but makes dlt a de facto dependency and conflates "Filedge ships a Fetcher example" with "Filedge endorses one fetch tool." dlt remains a valid *alternative* Fetcher, not the reference.

## Consequences

- Operators get a runnable, auditable Fetcher example and reusable building blocks (Source Manifest emission, Fetch-Lock promotion) for writing their own Python Fetchers.
- The product message is unchanged: Filedge is the reliability/audit/Commit layer; fetching is upstream plumbing.
- The Reference Fetcher's API coverage is intentionally minimal (one open source). Growing it to fintech SaaS APIs is evidence-driven and additive, gated behind the source-client seam and the `fetch` extra.
