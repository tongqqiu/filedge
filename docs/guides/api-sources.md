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

## Example: Stripe Events -> S3 -> BigQuery

### 1. Fetch to files

Configure your Fetcher to write complete NDJSON files to a staging prefix, then promote them to the Watched Directory:

```
s3://my-bucket/api-staging/stripe/
  stripe_events_20260522T140000_0001.ndjson.tmp

s3://my-bucket/landing/stripe/
  stripe_events_20260522T140000_0001.ndjson
```

The exact Fetcher command is intentionally outside Filedge's contract. For dlt, that may be a small project-specific Python script. For another organization, it may be a scheduled vendor export or an internal platform job.

### 2. Ingest

```bash
filedge run \
  --watched-dir s3://my-bucket/landing/stripe/ \
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

destination_table: stripe_events
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
  --watched-dir /data/landing/salesforce/ \
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
  ├── filedge run --watched-dir s3://.../landing/stripe/    --config pipeline.yaml ...
  └── filedge run --watched-dir s3://.../landing/salesforce/ --config pipeline.yaml ...
```

**Airflow:**

```python
fetch_stripe = BashOperator(
    task_id="fetch_stripe",
    bash_command="run-stripe-fetcher --output s3://.../landing/stripe/",
)
ingest_stripe = BashOperator(
    task_id="ingest_stripe",
    bash_command="filedge run --watched-dir s3://.../landing/stripe/ --config pipeline.yaml ...",
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
