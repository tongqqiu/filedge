# Changelog

All notable changes to Filedge are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (pre-1.0:
minor versions may include new features, patch versions are fixes).

The curated highlights below are the GitHub Release body; the full list of
merged pull requests is appended automatically beneath them on each release.

## [Unreleased]

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

[Unreleased]: https://github.com/tongqqiu/filedge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tongqqiu/filedge/compare/v0.1.2...v0.2.0
