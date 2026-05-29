# Author a pipeline

`filedge author` launches a local **Authoring UI** — a terminal app that walks you through [Pipeline Authoring](../../CONTEXT.md#pipeline-authoring) from a sample File to a ready-to-run [Pipeline Folder](../../CONTEXT.md#pipeline-folder). It reuses the exact same parsing, [Schema Inference](../../CONTEXT.md#schema-inference), config loading, and validation behavior as the rest of the CLI, so what it generates can't drift from what `filedge run` consumes.

It is **local and CLI-adjacent**, not a hosted service. It produces or reviews Pipeline Configs; it never runs ingestion, mutates [Audit Records](../../CONTEXT.md#audit-record), or stores secrets. See [ADR-0015](../adr/0015-control-and-audit-platform-starts-with-local-pipeline-authoring.md) and [ADR-0016](../adr/0016-authoring-ui-textual-tui.md).

## Install

The Authoring UI ships behind an optional extra so the core CLI stays lightweight:

```bash
uv sync --extra authoring
```

## Launch

```bash
filedge author orders.csv
```

Format is auto-detected from the extension (`.csv`, `.ndjson`/`.jsonl`, `.parquet`, `.xlsx`). Use `--format` to override, e.g. for an extensionless or fixed-width file:

```bash
filedge author layout.dat --format fixed_width
```

The destination table defaults to the sample File's stem (`orders.csv` → `orders`); override it with `--dest-table`.

## The Authoring Workflow

The [Authoring Workflow](../../CONTEXT.md#authoring-workflow) starts from a sample File rather than a blank form, because the File is the atomic unit of Filedge ingestion:

1. **Preview** — the UI reads a few rows so you can confirm you picked the intended input.
2. **Schema review** — [Schema Inference](../../CONTEXT.md#schema-inference) proposes a column for each field with its inferred [Column Type](../../CONTEXT.md#column-type), `required` flag, and [Confidence Tier](../../CONTEXT.md#confidence-tier). Edit source name, destination name, type, and required inline.
3. **Settings** — choose the [Write Mode](../../CONTEXT.md#write-mode), the [Connector](../../CONTEXT.md#connector), and (optionally) per-column [Field Encryption](../../CONTEXT.md#field-encryption).
4. **Validate** — run [Authoring Validation](../../CONTEXT.md#authoring-validation) and read a green/red result.
5. **Generate** — on green and explicit confirmation, write a [Pipeline Folder](../../CONTEXT.md#pipeline-folder) and create/update the [Pipeline Registry](../../CONTEXT.md#pipeline-registry).

## Keys

| Key | Action |
|-----|--------|
| `s` / `d` / `t` | Edit the focused column's **s**ource / **d**estination / **t**ype |
| `r` | Toggle the focused column's `required` flag |
| `a` | Acknowledge a low/ambiguous Confidence Tier |
| `o` | Edit the selected non-secret connector setting |
| `b` / `e` | Edit the CDC business key / sequence column (when `write_mode: cdc`) |
| `E` / `H` | Declare an **E**ncrypt / **H**ash key reference on the focused column |
| `D` | **D**uplicate the focused column under a new destination name |
| `X` | Clear Field Encryption from the focused column |
| `v` | Run Authoring Validation |
| `g` | Generate the Pipeline Folder |
| `q` | Quit |

Write Mode and Connector are chosen from dropdowns in the side panel.

## Schema review and Confidence Tiers

Every **low** and **ambiguous** Confidence Tier column must be acknowledged (`a`) before generation — this is how risky inference choices become explicit and auditable. The acknowledgement and its evidence (null count, sample size, inference notes) are recorded in the [Authoring Runbook](../../CONTEXT.md#authoring-runbook). High-confidence columns need no acknowledgement.

See the [inspect guide](inspect.md#confidence-tiers) for what each tier means.

## Write Mode

Pick `append` (default), `truncate`, or `cdc` from the Write Mode dropdown. Selecting `cdc` reveals the CDC settings; press `b` to set the business key column(s) and `e` to set the sequence column. Authoring Validation reports missing CDC settings as failures before you can generate. See the [CDC files guide](cdc-files.md).

## Connector and Credential Placeholders

Choose a [Connector](../../CONTEXT.md#connector) from the dropdown and fill in its required **non-secret** settings (press `o`). Credentials are never collected: the UI shows the [Credential Placeholders](../../CONTEXT.md#credential-placeholder) — the environment variable names the connector expects at runtime — and records them in the Runbook. Required non-secret settings must be present before generation.

## Field Encryption

You can declare per-column [Field Encryption](../../CONTEXT.md#field-encryption) so plaintext PII never reaches the warehouse:

- Press `E` on a column to declare an `encrypt:` block (AES-256-GCM, randomized).
- Press `H` to declare a `hash:` block (HMAC-SHA256, a one-way joinable token).
- A column may declare **neither, one, or both**.
- Press `D` to duplicate a column under a new destination name when one source column needs to land both encrypted **and** hashed.
- Press `X` to clear the declarations.

The key reference you enter is a **Credential Placeholder** — `env:NAME` or `secrets:/absolute/path` — not key material. The Authoring UI never collects, stores, tests, or exports a key: Filedge resolves it from the environment or a secrets mount at run time ([ADR-0014](../adr/0014-column-level-field-encryption.md)). Authoring Validation checks only the *structural* validity of the declarations (e.g. `encrypt:` requires `type: string`); a bad shape is reported red before generation.

## What Authoring Validation does — and doesn't — cover

Authoring Validation proves the sample File and the Pipeline Config are compatible under the [Validation Scope](../../CONTEXT.md#validation-scope): Parser readability, [Column Tolerance](../../CONTEXT.md#column-tolerance), [Strict Mode](../../CONTEXT.md#strict-mode) type coercion, structural Field Encryption validity, Write Mode required settings, and config loading.

It deliberately **excludes** Destination reachability, production credentials, and destination table readiness. A green result does not promise production readiness — that boundary belongs to [`filedge healthcheck`](healthcheck.md), and the UI says so after generation.

## Generated artifacts

Generation writes a Pipeline Folder under `pipelines/<id>/` in the workspace (`--workspace`, default `.`):

```
pipelines/orders/
├── pipeline.yaml      # the exact artifact filedge run consumes
└── RUNBOOK.md         # non-secret Authoring Runbook
```

and creates or updates `pipeline-registry.yaml` at the workspace root.

- **`pipeline.yaml`** round-trips through the same config loader the Operator CLI uses — it is validated before anything lands on disk.
- **`RUNBOOK.md`** is a non-secret note recording the sample File (by path, never copied), accepted Confidence Tiers, Credential Placeholders, declared Field Encryption columns (key *references* only), validation assumptions, and the suggested next commands. No environment variable is ever read, so no secret can bleed into an artifact.
- **`pipeline-registry.yaml`** indexes each Pipeline's Folder, Watched Directory, Audit DB connection placeholder, and Audit Export destination. It keeps Audit DBs separate — one [Audit DB](../../CONTEXT.md#audit-db) maps to exactly one Pipeline. See [ADR-0017](../adr/0017-pipeline-folder-and-registry-layout.md).

## Next steps

After generation the UI prints the Operator CLI handoff. The Authoring UI hands off to the CLI; it does not run, schedule, or deploy anything:

```bash
filedge validate orders.csv --config pipelines/orders/pipeline.yaml
filedge healthcheck --config pipelines/orders/pipeline.yaml --audit-db-url "$ORDERS_AUDIT_DB_URL"
filedge run --dir ./landing/orders --config pipelines/orders/pipeline.yaml --audit-db-url "$ORDERS_AUDIT_DB_URL"
filedge export-audit --audit-db-url "$ORDERS_AUDIT_DB_URL" --output ./audit-exports/orders/index.html
```

Run `filedge healthcheck` to confirm Destination reachability before the first real Run.

## Format notes

The Authoring UI supports every format the Parser does:

- **CSV / NDJSON / Parquet / Excel** — schema is inferred from the sample. For Excel, a sheet picker appears for multi-sheet workbooks (or pass `--sheet`).
- **Fixed-width** — no schema can be inferred from the file ([ADR-0013](../adr/0013-fixed-width-format-support.md)), so launch with `--format fixed_width` and enter the [Fixed-Width Layout](../../CONTEXT.md#fixed-width-layout) (`start`/`width` per column) from the partner record-layout spec. See the [fixed-width guide](fixed-width.md).

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--format` | auto from extension | `csv`, `ndjson`, `parquet`, `excel`, or `fixed_width` |
| `--sample-rows` | 1000 | Number of rows to sample for Schema Inference |
| `--dest-table` | sample File stem | Destination table name |
| `--out` | from `--dest-table` | Pipeline Folder id/name override |
| `--workspace` | `.` | Workspace root for the Pipeline Folder and Pipeline Registry |
| `--encoding` | auto | File encoding override |
| `--sheet` | first sheet | Excel sheet name or 0-based index (excel format only) |
