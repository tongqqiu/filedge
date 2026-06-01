# Stripe-style API to audited DuckDB demo

This is the local golden path for Filedge:

```text
Stripe-style API -> filedge-fetch -> complete NDJSON File + Source Manifest
                 -> filedge run -> DuckDB Destination + Audit DB
                 -> lineage / Audit Export
```

It uses DuckDB so the whole flow is runnable on a laptop. Swap the Connector to
Snowflake when you want the same contract against a production warehouse.

## What this proves

By the end, you will have:

- pulled Stripe-style `charges` through the Reference Fetcher;
- materialized the API response as one complete NDJSON File;
- landed a Source Manifest next to that File;
- ingested the File into DuckDB through `filedge run`;
- traced the loaded File back to the API Source range;
- generated a static Audit Export for review.

## 1. Install the local extras

```bash
uv sync --extra dev --extra duckdb
```

For a no-account run, use `stripe-mock`:

```bash
docker run --rm -p 12111:12111 stripe/stripe-mock:latest
```

Set any test key; the mock only needs the header shape.

```bash
export STRIPE_API_KEY=sk_test_anything
```

## 2. Create the demo workspace

```bash
mkdir -p /tmp/filedge-stripe-demo
cd /tmp/filedge-stripe-demo
```

Copy the example configs from a Filedge checkout:

```bash
cp /path/to/filedge/examples/stripe-duckdb/sources.yaml ./sources.yaml
cp /path/to/filedge/examples/stripe-duckdb/pipeline.yaml ./pipeline.yaml
```

Both files use `./stripe-demo/...` paths, so they work from this workspace.

## 3. Fetch charges into Files

```bash
uv run filedge-fetch --config ./sources.yaml --source stripe-charges
```

Inspect the Watched Directory:

```bash
ls ./stripe-demo/landing
```

You should see one `.ndjson` data File and one matching
`.ndjson.manifest.json` Source Manifest. The API response is now a complete,
auditable File before it touches the Destination.

## 4. Ingest into DuckDB

```bash
uv run filedge run \
  --dir ./stripe-demo/landing \
  --config ./pipeline.yaml \
  --audit-db-url sqlite:///./stripe-demo/audit.db \
  --no-progress
```

The Destination rows land in:

```text
./stripe-demo/stripe.duckdb
```

The File audit trail lands in:

```text
./stripe-demo/audit.db
```

## 5. Check status

```bash
uv run filedge status \
  --audit-db-url sqlite:///./stripe-demo/audit.db
```

You should see the File counted as `COMMITTED`.

## 6. Trace lineage

Pick the landed File:

```bash
FILE=$(basename "$(ls ./stripe-demo/landing/*.ndjson | head -1)")
```

Then ask Filedge for lineage:

```bash
uv run filedge lineage "$FILE" \
  --audit-db-url sqlite:///./stripe-demo/audit.db \
  --dest-table stripe_charges
```

The lineage output includes `source_type: stripe`, `source_name:
stripe-charges`, the record count, and the covered cursor range. This is the
audit handoff: a loaded File can explain where it came from.

## 7. Export audit evidence

```bash
uv run filedge export-audit \
  --audit-db-url sqlite:///./stripe-demo/audit.db \
  --output ./stripe-demo/audit-export/index.html \
  --title "Stripe Charges" \
  --dest-table stripe_charges
```

Open `./stripe-demo/audit-export/index.html` to review the static Audit Export.
The Audit DB remains the system of record; the export is a read-only snapshot.

## Production variant: Snowflake

The flow stays the same. Change only the Connector block and runtime
credentials:

```yaml
connector:
  type: snowflake
  account: your-account
  user: FILEDGE_LOADER
  warehouse: LOAD_WH
  database: RAW
  schema: STRIPE
  role: LOADER
```

Set `SNOWFLAKE_PASSWORD` at runtime, run `filedge healthcheck`, then run the
same `filedge run` command. The Source Manifest, Content Hash deduplication,
Audit Record, and row-level provenance do not change.
