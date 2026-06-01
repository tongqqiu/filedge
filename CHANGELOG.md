# Changelog

All notable changes to Filedge are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (pre-1.0:
minor versions may include new features, patch versions are fixes).

The curated highlights below are the GitHub Release body; the full list of
merged pull requests is appended automatically beneath them on each release.

## [Unreleased]

### Added

- **Reference deployment** under `deploy/` — a slim container image
  (`deploy/Dockerfile`, non-root, build-arg extras) and a runnable
  `docker compose` stack demonstrating the two-job fetch + run pattern against
  the open EDGAR → SQLite path (zero credentials). A new
  [Deploy guide](docs/guides/deploy.md) documents the image, compose, and the
  Kubernetes CronJob production pattern.
- Snowflake **key-pair (RSA) authentication**, preferred over password — set
  `SNOWFLAKE_PRIVATE_KEY_PATH` (and `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` if the key
  is encrypted). Required on Snowflake accounts where single-factor password
  sign-in is disabled; `SNOWFLAKE_PASSWORD` remains a fallback.

## [0.5.0] - 2026-05-31

This release widens Filedge's source and destination coverage for the fintech
Target User: a first-party Stripe API Source upstream, and a Snowflake
destination connector downstream — both behind their respective seams, with no
change to the core ingestion path.

### Highlights

- **Snowflake connector** (`type: snowflake`) — Filedge now loads into Snowflake
  alongside SQLite, PostgreSQL, BigQuery, Databricks, and DuckDB. Content-hash
  idempotency comes from a per-hash `DELETE` + batched `INSERT` in one
  transaction (re-loading a File is a no-op; a failed load rolls back), with
  transactional CDC. Identifiers are double-quoted so names land in Snowflake
  exactly as written in `pipeline.yaml`.
- **First-party Stripe API Source** (`type: stripe`) — the Reference Fetcher can
  pull a Stripe-style list API directly: cursor pagination (`starting_after` /
  `has_more`), bearer auth, and an incremental `created[gt]` cursor. Proves the
  auth + pagination + rate-limit case that the open EDGAR source did not.

### Added

- Snowflake connector (`snowflake` extra). The password is supplied at runtime
  via `SNOWFLAKE_PASSWORD`, never in `pipeline.yaml`. Unit-tested for SQL
  generation; a gated `Snowflake Integration` workflow runs the live round trip.
- Stripe API Source for `filedge-fetch`. `api_base` can point at `stripe-mock`
  for credential-free runs, so the source is exercisable without an account.

### Documentation

- The connectors reference and README document the Snowflake connector.
- The API Source adapter guide was rewritten to match the real extension pattern
  (it had described modules that did not exist), with Stripe as the worked
  example; `dest_table` examples corrected (were `destination_table`).

## [0.4.0] - 2026-05-31

This release makes Dead-Letter Quarantine operable end-to-end, sharpens Schema
Inference, and hardens the client-facing guides so they stay accurate.

### Highlights

- **Quarantine is operable end-to-end.** A quarantined File is now visibly
  distinct in the Audit Export — its quarantined-row count and sidecar path are
  shown, so a partial commit no longer reads as a clean `COMMITTED` File. A new
  `filedge redrop-quarantine` command unwraps a quarantine sidecar back into a
  clean, re-droppable NDJSON File, so corrected rows re-ingest on the normal
  audited path under a new Content Hash.
- **Sharper Schema Inference.** `filedge inspect` now reports a clean text
  column as **high** confidence instead of flagging every text column
  **ambiguous**, so the review signal means something. `string` is reported
  ambiguous only on genuinely conflicting evidence — mixed numeric/text values,
  mixed or non-ISO date formats, or nested objects/arrays (ADR-0008 amended).

### Added

- `filedge redrop-quarantine --sidecar <path>` — re-drop a quarantine sidecar
  as a clean NDJSON File, with an optional `--pipeline`/`--config` NDJSON
  re-drop compatibility check.
- Quarantine surfaced in the Audit Export: per-File quarantined-row count and a
  quarantine-sidecar path, badged distinctly from a clean commit.

### Fixed

- `filedge compact` now creates its `--output` directory when it does not exist,
  instead of failing with a cryptic "No such file or directory".
- `filedge inspect` no longer labels clean text columns `ambiguous`; its YAML
  header is also correctly branded `filedge inspect` (was `etl inspect`).

### Documentation

- New [Quarantine guide](docs/guides/quarantine.md) with a runnable full-loop
  walkthrough (partial commit → status → Audit Export → investigate → re-drop)
  and a DuckDB/jq sidecar-investigation recipe.
- Adoption accuracy pass: the Getting Started, inspect, preview, run, and EDGAR
  demo guides were corrected against real CLI output.
- An executable guide walkthrough harness runs each guide's commands through the
  real CLI in CI, so the guides can no longer drift silently.
- ADR-0020 (Iceberg table format via the Materializer) and ADR-0021 (companion
  output as NDJSON/Parquet at the boundary) recorded.

## [0.3.0] - 2026-05-30

This pre-release line expands Filedge from the ingestion core into a fuller
Control and Audit Platform surface: Pipeline Registry-aware operator commands,
first-party companion jobs for API and Queue sources, and opt-in
Dead-Letter Quarantine for bounded bad-row handling.

### Highlights

- **Pipeline Registry-aware Operator CLI** — `filedge run`, `status`,
  `lineage`, `requeue`, and `export-audit` can resolve Pipeline Config,
  Watched Directory, Audit DB, and Audit Export destination from a Pipeline
  Registry with `--pipeline <id>`. `filedge status --all` fans out across every
  registered Pipeline while keeping each Audit DB independent.
- **Reference Fetcher (`filedge-fetch`)** — an external companion that pulls an
  API Source from a Sources Config, stages a complete NDJSON File, emits a
  Source Manifest, promotes under a Fetch Lock, and advances the cursor only
  after promotion. It now supports generic HTTP/GitHub-style sources and SEC
  EDGAR `companyConcept` sources with required `User-Agent` headers and
  client-side incremental filtering by `filed`.
- **Reference Queue Materializer (`filedge-materialize`)** — an external
  companion for Kafka Queue Sources. It materializes per-partition
  Micro-batches into complete NDJSON Files with Offset Range Metadata, supports
  Drain and Continuous Trigger Modes, and commits broker offsets only after
  promotion.
- **Dead-Letter Quarantine** — an opt-in Pipeline policy that commits good rows
  while writing bad rows to an accounted quarantine sidecar, guarded by
  configured failure thresholds so Strict Mode remains the default signal.

### Added

- `--pipeline <id>` / `--workspace <path>` resolution for Run, Status,
  Lineage, Requeue, and Audit Export workflows.
- `filedge status --all` for Registry-wide status summaries.
- `filedge-fetch` console script, Sources Config loader, HTTP source client,
  cursor store, staging/manifest/promotion flow, and EDGAR source support.
- `filedge-materialize` console script, Kafka Sources Config loader, JSON
  decoder, Queue Consumer, Drain and Continuous Trigger Modes, and the `kafka`
  optional extra.
- Shared `filedge.companion` modules for staged NDJSON writing, Source Manifest
  emission, and Fetch Lock promotion.
- `source_manifest:` parsing on file registration with lineage/status
  visibility for Source Manifest metadata.
- Dead-Letter Quarantine config, processor, sink, audit fields, CLI/status/
  lineage display, and ADR-0019.

### Changed

- Pipeline Authoring gained Re-Author support for existing Pipeline Folders,
  Registry browse-and-pick, sample refresh, and Authoring Validation Drift
  feedback.
- `filedge run` and companion jobs share the same materialized-File contract:
  complete Files plus optional Source Manifests reach the Watched Directory;
  source mechanics remain upstream.

### Documentation

- New guides for [API sources](docs/guides/api-sources.md),
  [API Source adapters](docs/guides/api-source-adapters.md),
  [EDGAR API demo](docs/guides/edgar-demo.md),
  [Queue sources](docs/guides/queue-sources.md), and
  [Dead-Letter Quarantine](docs/adr/0019-dead-letter-quarantine.md).
- CLI reference updated for Pipeline Registry resolution, `filedge-fetch`, and
  `filedge-materialize`.
- ADR-0018 and ADR-0019 added to the architecture decisions index.

## [0.2.0] - 2026-05-29

The first **Control and Audit Platform** surface lands: a local Pipeline
Authoring UI. Plus column-level Field Encryption and two new file formats.

### Highlights

- **Authoring UI (`filedge author`)** — a local terminal app for
  [Pipeline Authoring](docs/guides/author.md). Start from a sample file, review
  the inferred schema and Confidence Tiers, choose Write Mode, Connector, and
  per-column Field Encryption, run Authoring Validation, and generate a Pipeline
  Folder (`pipeline.yaml` + non-secret Authoring Runbook) with a Pipeline
  Registry. Ships behind the optional `authoring` extra; it never runs
  ingestion, mutates Audit Records, or stores secrets (ADR-0015, ADR-0016,
  ADR-0017).
- **Column-level Field Encryption** — declare per-column `encrypt:`
  (AES-256-GCM, randomized) and/or `hash:` (HMAC-SHA256) blocks in
  `pipeline.yaml` so plaintext PII never reaches the warehouse. Key material is
  supplied at runtime via environment variable or secrets mount, never stored in
  YAML (ADR-0014).
- **Excel (`.xlsx`) format** — inspect, preview, validate, and ingest `.xlsx`
  workbooks via the optional `excel` extra, with a `--sheet` selector for
  multi-sheet files (ADR-0012).
- **Fixed-width format** — ingest fixed-width text files with the column layout
  (`start`/`width`) declared in `pipeline.yaml` from the partner record-layout
  spec (ADR-0013).

### Added

- `filedge author` command and the `authoring` optional extra (Textual TUI).
- Headless authoring modules: Authoring Session, Pipeline Config Draft builder,
  Authoring Validation service, Pipeline Folder writer, Authoring Runbook
  renderer, and Pipeline Registry reader/writer.
- `format: excel` and `format: fixed_width` parsers.
- `encrypt:` / `hash:` column blocks for Field Encryption.

### Changed

- `inspect` / `preview` / `validate` now share a single File Sample Reader.
- Pipeline Config is bound to Parser keyword arguments in one place.
- Destination CDC apply consolidated into one path.
- Audit record read model and file registration extracted into focused modules.

### Documentation

- New [Author guide](docs/guides/author.md) and `filedge author` CLI reference.
- ADR-0012 through ADR-0017 added to the architecture decisions index.
- Versioned documentation site deployment.

[Unreleased]: https://github.com/tongqqiu/filedge/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/tongqqiu/filedge/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/tongqqiu/filedge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/tongqqiu/filedge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tongqqiu/filedge/compare/v0.1.2...v0.2.0
