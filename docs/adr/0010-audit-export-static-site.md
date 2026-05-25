# ADR-0010: Operator-Facing UI is a Read-Only Static Export

**Status:** Accepted

## Context

Filedge's target users are data engineering teams at fintech companies where auditability is a compliance requirement. Compliance officers and auditors need to trace destination rows back to their source Files and verify completeness — a job the Operator CLI cannot serve because those stakeholders do not use a terminal.

Two architectural decisions were in play:

1. **Deploy model**: a long-running HTTP server (`filedge serve`) vs. a static HTML site generated after each Run.
2. **Write capability**: read-only vs. exposing state-changing operations (re-queue, retry-cap override) through the UI.

## Decision

The operator-facing UI is a **read-only static HTML site** (`filedge export-audit`) generated as a batch step after `filedge run` and hosted as a static artifact (S3, GCS, or internal CDN). It overwrites the previous export on each run; the Audit DB is the system of record for point-in-time evidence.

All state-changing operations remain exclusively in the Operator CLI.

## Consequences

- The UI has zero runtime to operate: no daemon to monitor, restart, or secure. It fails or succeeds at export time, not at read time.
- Auth and access control are delegated to the static hosting layer (signed URLs, SSO-gated CDN, IP allowlist) — Filedge does not build login screens or session management.
- Every File state change in the Audit Record has exactly one origin: a `filedge run`. "Who changed this?" is always answerable without checking a second interface.
- `filedge export-audit` requires no changes to the export pipeline when the Audit DB schema changes: regenerate the site, get the current truth.
- Future multi-pipeline aggregation can be addressed at the export layer (a separate tool crawling per-pipeline export paths) without requiring an Audit DB schema change (`pipeline_id` column, shared audit DB).
- Static exports can be attached to compliance findings or archived as point-in-time evidence by taking a DB snapshot and re-running `filedge export-audit` against it.

## Alternatives Considered

**`filedge serve` — long-running HTTP server.** Introduces a daemon to operate, monitor, and version. Auth becomes an in-scope problem for the Filedge codebase. Conflicts with Filedge's batch identity — "short-lived process triggered by external scheduler" — by adding a persistent process with its own lifecycle.

**Read-write UI (re-queue, reset, pause from browser).** Makes the UI a second control plane alongside the CLI. Every state-changing primitive must be designed, tested, and documented twice. The audit trail gains ambiguity: state changes can arrive from `filedge run`, the CLI, *or* the UI. The CLI's role as "the stable interface" is diluted.

**Datasette or similar SQLite explorer.** A server, not a static export. Does not fit the batch deploy model and would require operators to run a separate long-lived process.

**Sample rows in the export.** Pulls PII into a static artifact that gets archived, emailed, and copied. For fintech pipelines this creates data-residency and right-to-be-forgotten exposure at scale. Warehouse lineage SQL (a copyable `SELECT ... WHERE _source_file_hash = '...'`) gives auditors the row-level answer without Filedge ever touching row data after ingestion.
