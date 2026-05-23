# Ingesting API Sources (SaaS Tools)

API data from SaaS tools (Stripe, Salesforce, HubSpot, Jira, GitHub, etc.) passes through the same pipeline as file drops — PENDING → COMMITTED, content-hash deduplication, row-level provenance, `filedge status` visibility. See ADR-0006.

The pattern:

```
filedge fetch  →  staging prefix  →  Watched Directory  →  etl run  →  Destination
```

`filedge fetch` handles the API pull, staging, and promotion to the Watched Directory. `filedge run` handles ingestion identically to any file-drop source.

---

## Prerequisites

```bash
pip install dlt[s3]       # or dlt[gcs], dlt[filesystem]
pip install dlt[stripe]   # replace with the source you need
```

dlt source packages: https://dlthub.com/docs/dlt-ecosystem/verified-sources

---

## Configuration

Each API Source has its own `sources.yaml`. Credentials are never in the file — dlt reads them from environment variables automatically.

```yaml
# stripe-sources.yaml
source:
  type: stripe                 # dlt source package name
  endpoints:
    - Event
    - Customer
    - Invoice
  incremental_key: created     # field dlt uses for cursor tracking

staging_prefix: s3://my-bucket/api-staging/stripe/
```

```yaml
# salesforce-sources.yaml
source:
  type: salesforce
  endpoints:
    - Opportunity
    - Account
  incremental_key: LastModifiedDate

staging_prefix: s3://my-bucket/api-staging/salesforce/
```

---

## Example: Stripe Events → S3 → BigQuery

### 1. Set credentials

```bash
export STRIPE_API_KEY=sk_live_...
```

### 2. Fetch

```bash
filedge fetch \
  --config stripe-sources.yaml \
  --output s3://my-bucket/landing/stripe/
```

`filedge fetch` behaviour:

1. Checks `staging_prefix/.fetch.lock` — fails fast if a fetch is already running
2. Writes `.fetch.lock` (timestamp + worker identity)
3. dlt pulls from the Stripe API → NDJSON files land in `staging_prefix`
4. On success: moves staged files to `--output` (Watched Directory), deletes lock
5. On failure: deletes staged files, deletes lock, exits non-zero

### 3. Ingest

```bash
filedge run \
  --watched-dir s3://my-bucket/landing/stripe/ \
  --config      pipeline.yaml \
  --audit-db-url $FILEDGE_AUDIT_DB_URL
```

### 4. pipeline.yaml

```yaml
format: ndjson

connector:
  type: bigquery
  project: my-gcp-project
  dataset: raw

destination_table: stripe_events
write_mode: append

columns:
  - name: id
    type: string
  - name: type
    type: string
  - name: created
    type: timestamp
  - name: data
    type: string
```

---

## Example: Salesforce → local → PostgreSQL

```bash
export SALESFORCE_USERNAME=user@company.com
export SALESFORCE_PASSWORD=...
export SALESFORCE_SECURITY_TOKEN=...

filedge fetch \
  --config salesforce-sources.yaml \
  --output /data/landing/salesforce/

filedge run \
  --watched-dir /data/landing/salesforce/ \
  --config      pipeline.yaml \
  --audit-db-url $FILEDGE_AUDIT_DB_URL
```

---

## Scheduling

`filedge fetch` and `filedge run` are independent jobs. Schedule fetch before run. Different sources can run on different cadences.

**Cloud Scheduler / EventBridge:**

```
Every 15 min:
  ├── etl fetch --config stripe-sources.yaml --output s3://.../landing/stripe/

Every hour:
  ├── etl fetch --config salesforce-sources.yaml --output s3://.../landing/salesforce/

Every hour + 10 min:
  ├── etl run --watched-dir s3://.../landing/stripe/    --config pipeline.yaml ...
  └── etl run --watched-dir s3://.../landing/salesforce/ --config pipeline.yaml ...
```

**Airflow:**

```python
fetch_stripe = BashOperator(
    task_id="fetch_stripe",
    bash_command="etl fetch --config stripe-sources.yaml --output s3://.../landing/stripe/",
)
ingest_stripe = BashOperator(
    task_id="ingest_stripe",
    bash_command="etl run --watched-dir s3://.../landing/stripe/ --config pipeline.yaml ...",
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

Every destination row carries `_source_file_hash` linking it back to the exact dlt-produced file. An auditor asking "what Stripe data landed on 2026-05-22 and which rows did it produce?" can answer from the Audit DB alone.

---

## Responsibility boundary

| Concern | Owner |
|---|---|
| API authentication | dlt (reads env vars) |
| Pagination | dlt |
| Rate limiting | dlt |
| Incremental cursor (last fetched ID/timestamp) | dlt pipeline state |
| Concurrent fetch prevention (Fetch Lock) | `filedge fetch` |
| Partial-fetch atomicity (staging → Watched Dir) | `filedge fetch` |
| File-level deduplication (content hash) | `filedge run` |
| PENDING → COMMITTED state machine | `filedge run` |
| Row-level provenance (`_source_file_hash`) | `filedge run` |
| Retry on ingestion failure | `filedge run` |
| Operator visibility (`filedge status`) | `filedge run` |
