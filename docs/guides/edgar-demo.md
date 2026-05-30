# EDGAR API to audited SQLite demo

This walkthrough shows the full Filedge value proposition in one small demo:

```
SEC EDGAR API -> filedge-fetch -> complete NDJSON File + Source Manifest
              -> filedge run -> SQLite Destination + Audit DB
              -> status / lineage / audit evidence
```

The point is not EDGAR itself. The point is that an API pull becomes the same
audited File contract as every other Filedge ingestion path.

## What this proves

By the end, you will have:

- pulled one company's public EDGAR facts;
- materialized the API response as a complete NDJSON File;
- landed a Source Manifest next to that File;
- ingested the File into SQLite through `filedge run`;
- inspected operational status from the Audit DB;
- traced the loaded File back to the EDGAR source range.

That is the core promise: upstream mechanics can vary, but every load gets the
same File-level audit trail and row-level provenance.

## 1. Create a demo workspace

```bash
mkdir -p /tmp/filedge-edgar-demo
cd /tmp/filedge-edgar-demo
```

## 2. Create `sources.yaml`

This Sources Config tells the Reference Fetcher how to pull Apple's `Revenues`
facts from SEC EDGAR.

Use a real contact in `user_agent`; SEC requires a descriptive User-Agent.

```yaml
version: 1

sources:
  - name: apple-revenues
    type: edgar
    cik: 320193
    taxonomy: us-gaap
    concept: Revenues
    unit: USD
    user_agent: "Your Name your-email@example.com"
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      field: filed
    gzip: false
```

## 3. Create `pipeline.yaml`

This Pipeline Config describes the NDJSON facts that `filedge-fetch` will land.
`source_manifest: required` makes the demo stricter: a File without its Source
Manifest fails before any Destination write.

```yaml
format: ndjson
dest_table: edgar_revenues
source_manifest: required

connector:
  type: sqlite
  url: sqlite:///edgar-demo.db

columns:
  - source: filed
    dest: filed
    type: string
    required: true
  - source: end
    dest: period_end
    type: string
    required: false
  - source: fy
    dest: fiscal_year
    type: integer
    required: false
  - source: fp
    dest: fiscal_period
    type: string
    required: false
  - source: form
    dest: form
    type: string
    required: false
  - source: accn
    dest: accession
    type: string
    required: false
  - source: val
    dest: value
    type: float
    required: true
```

## 4. Fetch EDGAR facts into Files

From a Filedge checkout, run:

```bash
uv run filedge-fetch --config /tmp/filedge-edgar-demo/sources.yaml --source apple-revenues
```

Or, if Filedge is already installed in your environment:

```bash
filedge-fetch --config /tmp/filedge-edgar-demo/sources.yaml --source apple-revenues
```

Inspect the landing directory:

```bash
ls /tmp/filedge-edgar-demo/landing
```

You should see two files:

- one `.ndjson` data File;
- one matching `.ndjson.manifest.json` Source Manifest.

That is the first important moment: an API response is now a complete,
auditable File before it touches the Destination.

## 5. Ingest the File into SQLite

```bash
uv run filedge run \
  --dir /tmp/filedge-edgar-demo/landing \
  --config /tmp/filedge-edgar-demo/pipeline.yaml \
  --audit-db-url sqlite:////tmp/filedge-edgar-demo/audit.db \
  --no-progress
```

The Destination rows land in:

```text
/tmp/filedge-edgar-demo/edgar-demo.db
```

The File audit trail lands in:

```text
/tmp/filedge-edgar-demo/audit.db
```

The separation matters: Filedge can audit the File state even when the
Destination is a different system.

## 6. Check operational status

```bash
uv run filedge status \
  --audit-db-url sqlite:////tmp/filedge-edgar-demo/audit.db
```

You should see the File counted as `COMMITTED`.

This answers the operator question: **did this File load successfully?**

## 7. Trace lineage

Pick the landed File:

```bash
FILE=$(basename "$(ls /tmp/filedge-edgar-demo/landing/*.ndjson | head -1)")
```

Then ask Filedge for lineage:

```bash
uv run filedge lineage "$FILE" \
  --audit-db-url sqlite:////tmp/filedge-edgar-demo/audit.db
```

This answers the audit question: **where did this loaded File come from?**

The lineage output includes Source Manifest metadata, including the EDGAR source
type, source name, record count, and covered `filed` cursor range.

## 8. Inspect loaded rows

If you have the `sqlite3` CLI installed:

```bash
sqlite3 /tmp/filedge-edgar-demo/edgar-demo.db \
  "select filed, period_end, fiscal_year, fiscal_period, form, value
   from edgar_revenues
   order by filed desc
   limit 10;"
```

You can now connect the dots:

- EDGAR facts were pulled from an API;
- the Fetcher materialized them as a complete File;
- the File was ingested through the same audited path as a normal file drop;
- the Audit DB can explain what loaded and where it came from.

## 9. Run it again

Run the Fetcher again:

```bash
uv run filedge-fetch --config /tmp/filedge-edgar-demo/sources.yaml --source apple-revenues
```

When there are no newer facts beyond the stored cursor, the run is a clean
no-op. Nothing new is promoted, and no duplicate rows are introduced.

## Why this is useful

Without Filedge, API pulls, queue materializers, vendor exports, and file drops
usually grow separate operational stories: different logs, different retry
rules, different audit trails.

Filedge makes them converge:

- every source becomes a complete File;
- every File has a Content Hash;
- every File gets a `PENDING -> PROCESSING -> COMMITTED/FAILED` audit state;
- every destination row carries provenance;
- Source Manifests connect materialized Files back to upstream ranges.

That is why Filedge is useful: it makes the handoff from upstream systems to the
Destination repeatable, auditable, and boring.

## Related

- [Getting Started](../getting-started.md) — the 5-minute quickstart
- [API sources](api-sources.md) — the Fetcher pattern this tutorial uses
- [Source manifests](source-manifests.md) — the lineage you traced in step 7
- [Run a pipeline](run.md) — the ingestion half of the demo
