# Ingesting API-Sourced Files

Filedge does not fetch from SaaS APIs directly. It ingests **Files**.

For API sources such as Stripe, Salesforce, HubSpot, Jira, or GitHub, use an upstream Fetcher to land complete NDJSON files in a Watched Directory. Then run Filedge against that directory. This keeps API-sourced data on the same ingestion path as file drops: Content Hash deduplication, PENDING -> COMMITTED audit state, strict validation, row-level provenance, and `filedge status` visibility.

See ADR-0006.

The boundary:

```
API Source -> Fetcher -> staging area -> Watched Directory -> filedge run -> Destination
```

The Fetcher owns API behavior. Filedge starts when complete files appear in the Watched Directory.

---

## Fetchers

A Fetcher can be any tool or job that writes complete files:

- a dlt pipeline configured to write NDJSON files
- a vendor export job
- a custom script
- Airbyte or Meltano configured to write files
- an existing internal ingestion job

dlt is a useful option because it already handles many SaaS APIs, but it is not a Filedge dependency and it is not the loader of record. The loader of record is still `filedge run`.

The Fetcher must guarantee:

- only complete files are promoted into the Watched Directory
- failed or partial fetches remain in staging or are deleted
- filenames are unique enough for operators to understand where they came from
- file contents are stable once visible to Filedge

---

## The Reference Fetcher (`filedge-fetch`)

Filedge ships a runnable example of a correct Fetcher: `filedge-fetch`. It is an **external companion** to `filedge run`, not part of the core ingestion path and never a loader of record (see ADR-0018). It exists to show — and enforce — the contract above end to end.

Given a Sources Config, `filedge-fetch`:

1. reads the last incremental cursor for the API Source;
2. pages through the API (rate-limit aware) for records newer than that cursor;
3. writes them as one **complete** NDJSON File in a staging area;
4. emits an OpenLineage-shaped [Source Manifest](source-manifests.md) sidecar for that File;
5. promotes the sidecar **then** the data File into the Watched Directory under a **Fetch Lock** — so a File is never visible without its provenance, and two concurrent fetches cannot race partial files;
6. advances the cursor **only after** a successful promotion, so a crash retries the same window rather than skipping data.

```bash
# Pull one API Source into complete NDJSON Files + manifests in ./landing/
filedge-fetch --config examples/sources.yaml --source github-commits

# Preview the window and target file without fetching
filedge-fetch --config examples/sources.yaml --source github-commits --dry-run

# Then ingest exactly like any file drop
filedge run --dir ./landing --config pipeline.yaml --audit-db-url $FILEDGE_AUDIT_DB_URL
```

Run the two as independent scheduled jobs — the same two-job pattern recommended for SFTP sync in ADR-0005.

The reference targets the public GitHub REST API because it needs no credentials and exercises both pagination and a `since`-style cursor. The API-specific code sits behind an API Source adapter seam, so adapting it to a fintech API (Stripe, Plaid) is a new adapter plus Sources Config parsing, not a rewrite of the staging, promotion, manifest, or cursor logic. See [How to add an API Source adapter](api-source-adapters.md) for the extension pattern.

EDGAR is also supported by the Reference Fetcher through the SEC `companyConcept`
endpoint. It needs no API key, but SEC policy requires a descriptive
`User-Agent` contact string. EDGAR returns a whole concept document, so the
Fetcher applies the incremental cursor client-side by keeping facts whose
`filed` date is newer than the stored high-water mark.

For runnable client-facing walkthroughs, start with the [Stripe-style API to
audited DuckDB demo](stripe-duckdb-demo.md), then compare the no-credential
[EDGAR API to audited SQLite demo](edgar-demo.md).

### Sources Config

A Sources Config (`sources.yaml`) is a Fetcher-only file, separate from `pipeline.yaml`. It declares the endpoint, the incremental cursor, an optional environment-variable credential lookup, and the staging/state/landing paths. No secret is ever written into it.

```yaml
version: 1
sources:
  - name: github-commits
    type: github
    url: https://api.github.com/repos/tongqqiu/filedge/commits
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      param: since          # query param carrying the high-water mark
      field: commit.committer.date   # dotted path the next cursor is read from
    query:
      sha: main
    # credential_env: GITHUB_TOKEN   # optional; bearer token from this env var
    page_size: 100
    gzip: false

  - name: edgar-apple-revenues
    type: edgar
    cik: 320193
    taxonomy: us-gaap       # optional; defaults to us-gaap
    concept: Revenues
    unit: USD
    user_agent: "Your Company your-email@example.com"
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      field: filed          # client-side high-water mark in each fact
    gzip: false
```

The same example ships at `examples/sources.yaml`.

### EDGAR companyConcept workflow

Run the EDGAR source the same way as the GitHub source:

```bash
filedge-fetch --config examples/sources.yaml --source edgar-apple-revenues
filedge run --dir ./landing --config pipeline.yaml --audit-db-url $FILEDGE_AUDIT_DB_URL
```

For `cik: 320193`, `taxonomy: us-gaap`, and `concept: Revenues`, the loader
builds:

```text
https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json
```

The Fetcher extracts one NDJSON row per fact from `units.USD`, emits a Source
Manifest with `source_type: edgar` and the covered `filed` date range, promotes
the File under the Fetch Lock, then advances the cursor only after promotion.
The same pattern can support SEC `frames` later: a different URL builder and
record path, with the staging, manifest, promotion, and cursor rules unchanged.

A matching `pipeline.yaml` sketch for the emitted facts:

```yaml
format: ndjson
dest_table: edgar_company_facts

connector:
  type: sqlite
  url: sqlite:///filedge.db

columns:
  - source: filed
    dest: filed
    type: string
    required: true
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
  - source: val
    dest: value
    type: integer
    required: true
```

---

## Example: Stripe Events -> S3 -> BigQuery

### 1. Fetch to files

The Reference Fetcher has a first-party `stripe` source. Declare it in a Sources
Config — only the resource and the env var holding the secret key are required:

```yaml
version: 1
sources:
  - name: stripe-charges
    type: stripe
    resource: charges
    credential_env: STRIPE_API_KEY
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    # api_base: http://localhost:12111   # point at stripe-mock for a no-account run
```

Set the secret key in the named environment variable (never in the file) and fetch:

```bash
export STRIPE_API_KEY=sk_live_...
filedge-fetch --config sources.yaml --source stripe-charges
```

It pages the Stripe list API (`starting_after` / `has_more`), writes one complete
NDJSON File of the `data` records with a Source Manifest, promotes it under the
Fetch Lock, and advances the incremental `created` cursor only after promotion —
so a re-run fetches only newer charges (`created[gt]`). To try it without a Stripe
account, run [stripe-mock](https://github.com/stripe/stripe-mock) and set
`api_base` to it.

External fetchers (dlt, a scheduled vendor export, an internal platform job)
remain valid alternatives — anything that lands a complete NDJSON File works. See
[how to add an API Source](api-source-adapters.md).

### 2. Ingest

```bash
filedge run \
  --dir s3://my-bucket/landing/stripe/ \
  --config      pipeline.yaml \
  --audit-db-url $FILEDGE_AUDIT_DB_URL
```

### 3. Configure the file schema

`pipeline.yaml` describes the files that the Fetcher lands:

```yaml
format: ndjson

connector:
  type: bigquery
  project: my-gcp-project
  dataset: raw

dest_table: stripe_events
write_mode: append

columns:
  - source: id
    dest: id
    type: string
    required: true
  - source: type
    dest: type
    type: string
    required: true
  - source: created
    dest: created
    type: timestamp
    required: true
  - source: data
    dest: data
    type: string
    required: false
```

---

## Example: Salesforce -> local files -> PostgreSQL

An internal job or external Fetcher lands complete files:

```
/data/landing/salesforce/
  Account_20260522T150000.ndjson
  Opportunity_20260522T150000.ndjson
```

Then Filedge ingests those files:

```bash
filedge run \
  --dir /data/landing/salesforce/ \
  --config      pipeline.yaml \
  --audit-db-url $FILEDGE_AUDIT_DB_URL
```

---

## Scheduling

Schedule the Fetcher before `filedge run`. Different API sources can run on different cadences. Filedge does not need to know which tool produced the files.

**Cloud Scheduler / EventBridge:**

```
Every 15 min:
  ├── run-stripe-fetcher --output s3://.../landing/stripe/

Every hour:
  ├── run-salesforce-fetcher --output s3://.../landing/salesforce/

Every hour + 10 min:
  ├── filedge run --dir s3://.../landing/stripe/    --config pipeline.yaml ...
  └── filedge run --dir s3://.../landing/salesforce/ --config pipeline.yaml ...
```

**Airflow:**

```python
fetch_stripe = BashOperator(
    task_id="fetch_stripe",
    bash_command="run-stripe-fetcher --output s3://.../landing/stripe/",
)
ingest_stripe = BashOperator(
    task_id="ingest_stripe",
    bash_command="filedge run --dir s3://.../landing/stripe/ --config pipeline.yaml ...",
)
fetch_stripe >> ingest_stripe
```

---

## Audit visibility

Because API-sourced data passes through the same pipeline:

```bash
filedge status --audit-db-url $FILEDGE_AUDIT_DB_URL

PENDING:    0
PROCESSING: 0
COMMITTED:  142
FAILED:     1

Recent failures:
  stripe_20260522T140000_0001.ndjson: schema mismatch on column 'amount_decimal'
```

Every destination row carries `_source_file_hash` linking it back to the exact fetched file. An auditor asking "what Stripe data landed on 2026-05-22 and which rows did it produce?" can answer from Filedge's Audit DB and destination provenance columns without knowing which Fetcher produced the file.

---

## Responsibility boundary

| Concern | Owner |
|---|---|
| API authentication | Fetcher |
| Pagination | Fetcher |
| Rate limiting | Fetcher |
| Incremental cursor (last fetched ID/timestamp) | Fetcher |
| Concurrent fetch prevention | Fetcher or scheduler |
| Partial-fetch atomicity (staging -> Watched Directory) | Fetcher |
| File-level deduplication (content hash) | `filedge run` |
| PENDING → COMMITTED state machine | `filedge run` |
| Row-level provenance (`_source_file_hash`) | `filedge run` |
| Retry on ingestion failure | `filedge run` |
| Operator visibility (`filedge status`) | `filedge run` |

---

## Source Manifests (optional)

The Fetcher can write a `*.manifest.json` sidecar next to each NDJSON file to record the API cursor range, run ID, and producer identity:

```
landing/stripe/
  stripe_events_20260522T140000_0001.ndjson
  stripe_events_20260522T140000_0001.ndjson.manifest.json
```

Filedge reads the sidecar at registration and stores the metadata on the File's Audit Record. Then operators can drill in with `filedge lineage` or filter `filedge status --json` failures by API source.

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-22T14:00:30Z",
  "producer": "https://github.com/dlt-hub/dlt",
  "run": {
    "runId": "dlt-stripe-2026-05-22T14:00",
    "facets": {"_filedgeManifest": {"manifest_version": "1", "record_count": 1500}}
  },
  "job": {"namespace": "api", "name": "stripe.charges"},
  "inputs": [{
    "name": "https://api.stripe.com/v1/charges",
    "facets": {"_sourceRange": {
      "cursor_start": "ch_3OXa...",
      "cursor_end":   "ch_3OYz...",
      "endpoint": "/v1/charges"
    }}
  }]
}
```

See [Source Manifests](source-manifests.md) for the full schema, policy modes (`disabled` / `optional` / `required`), and validation error categories. Direct file drops without manifests continue to work unchanged under the default `optional` policy.

## Related

- [API Source adapters](api-source-adapters.md) — extend the Fetcher to a new API
- [Source manifests](source-manifests.md) — upstream lineage for fetched Files
- [Queue sources](queue-sources.md) — the same pattern for message brokers
- [EDGAR tutorial](edgar-demo.md) — see the Fetcher end to end
