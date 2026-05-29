# ADR-0017: Pipeline Folder Layout and Pipeline Registry Format

**Status:** Accepted

## Context

ADR-0015 commits Filedge to local Pipeline Authoring as the first Control and Audit Platform surface, and ADR-0016 settles that the Authoring UI is a Textual TUI launched by `filedge author`. Neither decides *where authored artifacts land on disk* or *how the workspace keeps track of more than one Pipeline*. Before the Pipeline Folder writer and the Pipeline Registry reader/writer (the deep modules behind the UI and the CLI) can be built, four on-disk questions must be settled:

1. **Pipeline Folder directory shape.** Where Pipeline Folders live in the workspace, how each is named, and what files they contain beyond `pipeline.yaml` and the Authoring Runbook.
2. **Authoring Runbook file format.** Markdown or something else.
3. **Pipeline Registry on-disk format.** YAML, JSON, SQLite, or other.
4. **Pipeline Registry location.** A workspace-root file, a hidden `.filedge/` directory, the user home, or elsewhere.
5. **Registry creation and growth.** How the Registry comes into being with the first authored Pipeline and how it gains additional Pipelines without ever combining Audit DBs.

These choices are long-lived. The Pipeline Folder and Pipeline Registry are the durable, version-controllable record of how a regulated ingestion Pipeline was authored; their shape determines whether reviewers can read the workspace at a glance and whether the one-Audit-DB-per-Pipeline rule survives contact with a multi-Pipeline workspace.

Hard constraints carried in from ADR-0015, the PRD (#139), and CONTEXT.md:

- One Audit DB maps to exactly one Pipeline. The Registry must **not** combine Audit DBs and must reference an independent **Audit DB connection placeholder** per Pipeline. The global `content_hash UNIQUE` constraint inside each Audit DB is what enforces idempotency, so two Pipelines must never resolve to the same Audit DB.
- The Registry references Pipeline Folders, Watched Directories, Audit DB connection placeholders, and Audit Export destinations.
- The Registry must reject malformed entries rather than load them.
- A Pipeline Folder contains at least `pipeline.yaml` and an Authoring Runbook.
- The Authoring Runbook is non-secret. No secret material — including live Audit DB connection strings with embedded credentials — is written to any authored artifact.

## Decision

Authored artifacts live in **visible, version-controllable files at the workspace root**: a `pipelines/` directory of Pipeline Folders and a single `pipeline-registry.yaml` index. The Authoring Runbook is **Markdown**. The Registry is **YAML**, created lazily with the first authored Pipeline and grown by appending one independent entry per Pipeline.

### Pipeline Folder directory shape

Pipeline Folders live under a `pipelines/` directory in the **workspace root** — the directory in which the Authoring User runs `filedge author`:

```
<workspace>/
  pipelines/
    orders/
      pipeline.yaml      # the Pipeline Config
      RUNBOOK.md         # the Authoring Runbook
    customers/
      pipeline.yaml
      RUNBOOK.md
  pipeline-registry.yaml
```

- **Location.** Under `pipelines/` at the workspace root, beside the Registry. Both are meant to be committed to version control and reviewed; nothing authored is hidden.
- **Naming.** Each Pipeline Folder is named by a **Pipeline id** — a slug derived from the destination table (`dest_table`) or from an explicit `filedge author --out <name>`. The slug is lowercased, with non-alphanumeric runs collapsed to single hyphens (e.g. `dest_table: Daily_Orders` → `pipelines/daily-orders/`). The id is the folder name and the Registry key; it must be unique within the workspace.
- **Required contents.** At minimum `pipeline.yaml` (the Pipeline Config, the artifact the Operator CLI already consumes) and `RUNBOOK.md` (the Authoring Runbook). The folder is allowed to gain other non-secret authoring artifacts later (for example a recorded Schema Inference report), but those two files are the contract every Pipeline Folder must satisfy.
- **No sample File copy.** The sample File that informed authoring is **referenced by path** in the Runbook, not copied into the folder, so the folder never accumulates source data or PII.

A Pipeline Folder gives each Pipeline a stable local home that the Registry can point at; the CLI keeps working unchanged via `filedge run --config pipelines/orders/pipeline.yaml`.

### Authoring Runbook format: Markdown (`RUNBOOK.md`)

The Authoring Runbook is a Markdown file named `RUNBOOK.md` inside the Pipeline Folder. Markdown is diff-friendly, renders directly on GitHub and in editors, matches the existing `docs/` and ADR convention, and adds no dependency. It records the sample File used, accepted low/ambiguous Confidence Tiers, required Credential Placeholders, the Validation Scope assumptions, and the suggested `filedge validate` / `filedge healthcheck` / `filedge run` / `filedge export-audit` commands — all non-secret. The Runbook explains how to operate the Pipeline; it does not schedule, run, or deploy it.

### Pipeline Registry format: YAML

The Pipeline Registry is a single YAML file. YAML is the format the Pipeline Config already uses, so the workspace has one serialization to read and review rather than two. It is human-readable, hand-editable, comment-friendly, and diffs cleanly in review — properties that matter because the Registry is audit-relevant metadata, not an opaque store. The Registry is a **passive index that is read and rewritten by Filedge**, never a running process or a query engine.

### Pipeline Registry location: `pipeline-registry.yaml` at the workspace root

The Registry is a single visible file, `pipeline-registry.yaml`, at the workspace root beside `pipelines/`. It is project-level (one workspace, one index, travels with the repository when cloned) and visible (an auditor or reviewer sees it in a plain directory listing and a `git status`). It is not placed in the user home, which would tie Pipelines to one machine user and lose the index on clone, and it is not hidden under `.filedge/`, which would conceal a record that exists precisely to be reviewed.

### Registry schema and per-Pipeline isolation

```yaml
version: 1
pipelines:
  - id: orders
    folder: pipelines/orders
    watched_directory: ./landing/orders
    audit_db: env:ORDERS_AUDIT_DB_URL
    audit_export: ./audit-exports/orders
  - id: customers
    folder: pipelines/customers
    watched_directory: gs://acme-landing/customers
    audit_db: env:CUSTOMERS_AUDIT_DB_URL
    audit_export: ./audit-exports/customers
```

- `version` — registry schema version, so the reader can evolve the format compatibly.
- `pipelines` — an ordered list; each entry is one Pipeline and references exactly the four things the constraint names.
- `id` — unique Pipeline id (also the Pipeline Folder name).
- `folder` — workspace-relative path to the Pipeline Folder (which holds `pipeline.yaml` and `RUNBOOK.md`).
- `watched_directory` — the Pipeline's landing zone (local path or cloud URI).
- `audit_db` — an **Audit DB connection placeholder**, following the same `env:`/`secrets:` placeholder convention as Credential Placeholders. The Registry stores *where the connection string comes from at runtime*, never the literal string, because a live Audit DB URL can embed credentials and no secret belongs in an authored artifact.
- `audit_export` — the Audit Export destination for this Pipeline (one Audit Export per Pipeline per Audit DB).

The Registry never embeds a Pipeline Config inline and never embeds Audit DB contents. It points at independent per-Pipeline artifacts; the one-Audit-DB-per-Pipeline rule is preserved by giving every entry its own `audit_db` placeholder.

### Registry creation and growth

- **Creation.** The Registry is created lazily. When `filedge author` writes the first Pipeline Folder and no `pipeline-registry.yaml` exists at the workspace root, Filedge creates one with `version: 1` and a `pipelines:` list holding that single entry.
- **Growth.** Authoring an additional Pipeline appends one new entry to `pipelines:`. Appending reads the existing Registry, adds the new entry, and rewrites the file; it never touches, merges, or rewrites the `audit_db` of any existing entry. Existing entries are preserved verbatim, so growth is additive and order-stable.
- **Validation on load (reject malformed entries).** The Registry reader rejects rather than silently tolerates a malformed Registry. It fails with a clear error when: a required field is missing; an `id` is duplicated; a `folder` does not exist or lacks `pipeline.yaml`; or — the load-bearing audit check — **two entries share the same `audit_db` placeholder**, which would point two Pipelines at one Audit DB and reintroduce cross-Pipeline deduplication. A duplicate Audit DB placeholder is treated as a malformed Registry, not a merge.

## Consequences

- **The workspace is readable at a glance.** `pipelines/<id>/{pipeline.yaml,RUNBOOK.md}` plus a root `pipeline-registry.yaml` is the whole authored surface. An auditor can read it without running Filedge, and it all lives in version control.
- **No secret ever lands in an authored artifact.** Audit DB connections are placeholders, exactly like Credential Placeholders; the Runbook is non-secret Markdown; sample Files are referenced, not copied. The Authoring UI stays clear of secret-management responsibilities.
- **Audit DB isolation is enforced mechanically.** Duplicate `audit_db` placeholders are a load error, so the Registry cannot quietly combine two Pipelines onto one Audit DB. This complements, and does not replace, the global `content_hash UNIQUE` constraint inside each Audit DB.
- **The Registry is a passive file, not a control plane.** Because it is YAML that Filedge reads and rewrites, it adds no daemon, no lock manager, and no second state-changing surface — consistent with the Operator CLI remaining the only state-changing interface.
- **Two serializations are avoided.** Pipeline Config and Registry are both YAML; the Runbook is Markdown like the rest of the docs. No new format is introduced.
- **Concurrent authoring is last-writer-wins.** Two simultaneous `filedge author` runs against one workspace can race on the rewrite. The Authoring UI is a single local user editing one workspace, so this is acceptable; a future ADR can add file locking if concurrent authoring ever becomes real.
- **Renames are a manual edit.** Changing a Pipeline id means renaming its folder and its Registry `id` together. The reader's existence check surfaces a stale `folder` pointer as a clear load error rather than a silent miss.

## Alternatives Considered

**Registry as SQLite.** A `.filedge/registry.db` SQLite catalog. Rejected because a binary store is merge-hostile and unreviewable in diffs, and because shipping a queryable database for the index edges toward the running, state-changing control plane ADR-0015 explicitly closes. The Registry must be a passive, human-readable file, not a second database beside each Pipeline's Audit DB.

**Registry as JSON.** A `pipeline-registry.json`. Rejected because JSON has no comments and is less pleasant to hand-edit and review, and because the Pipeline Config is already YAML — a second serialization format in the same workspace adds cognitive load for no benefit.

**Registry in the user home (`~/.filedge/registry.yaml`).** Rejected because it ties Pipelines to one machine user, breaks per-project portability, and is lost when the workspace is cloned to another machine — defeating the goal of one consistent, travelable workspace model.

**Registry hidden under `.filedge/`.** A `.filedge/registry.yaml` directory at the project root. Rejected as the first choice because hiding an audit-relevant index works against the review-first, compliance-oriented ethos of Filedge; the Registry exists to be seen, committed, and reviewed. A hidden workspace-metadata directory can be revisited later for genuinely incidental state (caches), not for the authored index.

**Single combined file with inline Pipeline Configs.** Put every Pipeline's full config inside the Registry rather than in per-Pipeline `pipeline.yaml` files. Rejected because it couples unrelated config edits into one file, fights the Pipeline Folder model the PRD requires, breaks the Operator CLI's existing `--config pipeline.yaml` contract, and centralizes what must stay per-Pipeline — the same instinct that combining Audit DBs is rejected for.

**Authoring Runbook as reStructuredText or generated HTML.** Rejected because Markdown is the lightest non-secret note format, diffs cleanly, renders on GitHub and in editors without tooling, and matches the existing ADR and docs convention. A generated HTML Runbook would also blur into Audit Export territory, which is deliberately the separate read-only compliance surface.
