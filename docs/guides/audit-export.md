# Audit Export

`filedge export-audit` generates a self-contained static HTML site from the Audit DB — a read-only compliance view for stakeholders who need to trace destination rows to source files without CLI access.

## Basic usage

```bash
filedge export-audit \
  --audit-db-url sqlite:///filedge.db \
  --output ./site/index.html
```

Or set the audit DB via environment variable:

```bash
export FILEDGE_AUDIT_DB_URL=sqlite:///filedge.db
filedge export-audit --output ./site/index.html
```

## Browsing locally with `filedge serve`

To skip picking an output path and just look at the audit trail in a browser, use `filedge serve` — the `dbt docs serve` analogue:

```bash
filedge serve --audit-db-url sqlite:///filedge.db --title "KYC Documents"
# Serving Audit Export (42 files) at http://127.0.0.1:8000/
```

It renders the same static Audit Export and serves it on localhost, opening a browser window (pass `--no-open` to suppress). The page is regenerated from the Audit DB on each request, so leaving `filedge serve` running while a scheduled `filedge run` ingests new files lets you refresh the browser to see them — no restart, no re-export. It binds to `127.0.0.1` by default (use `--host`/`--port` to change), only ever reads the Audit DB, and adds no control surface: every state-changing operation stays in the Operator CLI. Use `export-audit` when you want a file to publish or archive; use `serve` for an ad-hoc local look.

## Options

| Flag | Description |
|------|-------------|
| `--audit-db-url` | Audit DB connection string. Can also be set via `FILEDGE_AUDIT_DB_URL`. |
| `--output` | Path for the generated `index.html`. Parent directories are created if absent. |
| `--title` | Pipeline label shown in the site header — useful when hosting exports for multiple pipelines. |
| `--dest-table` | Destination table name. When provided, the lineage SQL in the export becomes immediately executable. |

## Scheduling

Run `filedge export-audit` as a step after `filedge run` in your scheduler:

```bash
# cron / Airflow / Kubernetes CronJob
filedge run --dir ./incoming --config pipeline.yaml
filedge export-audit --output ./site/index.html --title "KYC Documents" --dest-table kyc.documents
```

The site always reflects the current Audit DB state — it overwrites the previous export on each run. The Audit DB is the system of record for point-in-time evidence; re-run `export-audit` against a DB snapshot to reproduce any historical view.

## Hosting

The output is a single self-contained `index.html` — no server required. Upload it anywhere that serves static files:

```bash
# S3
aws s3 cp ./site/index.html s3://my-bucket/filedge/index.html

# GCS
gsutil cp ./site/index.html gs://my-bucket/filedge/index.html
```

Access control is handled by your static hosting layer (signed URLs, SSO-gated CDN, IP allowlist) — Filedge does not manage authentication.

## What the export shows

The site opens with a summary strip — total files, counts by state (`COMMITTED` / `PROCESSING` / `PENDING` / `FAILED`), rows loaded, and quarantined rows — the same figures `filedge status` prints. Below it is a sortable, filterable Files table. For each file:

- Filename, state chip (`COMMITTED` / `FAILED` / `PENDING` / `PROCESSING`)
- Row count committed to the destination (blank `—` for files ingested before row count tracking was added)
- Attempt count and last-updated timestamp
- Click to expand: full content hash, source directory, [Source Manifest](source-manifests.md) lineage (`source_type`, `source_name`, `producer`, `external_run_id`) when present, error message (for FAILED files), and a copyable lineage SQL query

### Lineage SQL

Each file row includes a **Copy lineage SQL** button that produces a query like:

```sql
SELECT *
FROM kyc.documents
WHERE _source_file_hash = 'a3f2b1c4...';
```

Paste this into your BI tool or warehouse query console to see every destination row that came from that specific file.

!!! note "Row data stays in the warehouse"
    The export never contains destination row data. The lineage SQL lets auditors query the warehouse directly — keeping PII out of the static artifact.

## Related

- [Run a pipeline](run.md) — populates the Audit DB this exports
- [Source manifests](source-manifests.md) — the upstream lineage in the export
- [Observability](observability.md) — live operational signals
