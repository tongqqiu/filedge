# Ingesting Queue-Sourced Files

Filedge does not consume directly from Kafka, SQS, Kinesis, or other message brokers. It ingests **Files**.

For queue sources, use an upstream Queue Materializer to land complete NDJSON or Parquet files in a Watched Directory. Then run Filedge against that directory. This keeps queue-sourced data on the same ingestion path as file drops and API exports: Content Hash deduplication, PENDING -> COMMITTED audit state, strict validation, row-level provenance, and `filedge status` visibility.

See ADR-0007.

The boundary:

```
Queue Source -> Queue Materializer -> staging area -> Watched Directory -> filedge run -> Destination
```

The Queue Materializer owns queue behavior. Filedge starts when complete files appear in the Watched Directory.

---

## Materializers

A Queue Materializer can be any tool or job that writes complete files:

- Kafka Connect S3 Sink or GCS Sink
- Flink or Spark Structured Streaming
- Vector or Benthos
- cloud-native delivery services
- a custom consumer

The materializer must guarantee:

- only complete files are promoted into the Watched Directory
- failed or partial writes remain in staging or are deleted
- file contents are stable once visible to Filedge
- queue provenance is recoverable from filenames, object metadata, or sidecar manifests

For Kafka, include topic, partition, and offset range where operators can find it:

```
s3://my-bucket/landing/orders/
  orders.topic0.0000000100-0000000199.ndjson
  orders.topic1.0000000040-0000000099.ndjson
```

Offset information is useful traceability metadata, but it is not Filedge's idempotency key. Filedge still deduplicates by Content Hash.

---

## The Reference Queue Materializer (`filedge-materialize`)

Filedge ships a runnable example of a correct Queue Materializer: `filedge-materialize`. It is an **external companion** to `filedge run`, not part of the core ingestion path and never a loader of record (see ADR-0007, ADR-0018). It is the queue mirror of the Reference Fetcher.

Given a kafka-typed [Sources Config](#sources-config), `filedge-materialize`:

1. consumes the topic into a **Micro-batch per partition** on a count-or-time boundary;
2. decodes each message (JSON by default) into a row;
3. writes the batch as one **complete** NDJSON File in a staging area;
4. emits an OpenLineage-shaped [Source Manifest](source-manifests.md) sidecar carrying the **Offset Range Metadata** (`{topic}.{partition}.{start}-{end}`);
5. promotes the sidecar **then** the data File into the Watched Directory under a **Fetch Lock**;
6. **commits the broker offset only after** a successful promotion — a crash re-consumes the same offset range rather than losing it.

Two **Trigger Modes**:

- **`drain`** (default) — snapshot each partition's high-water mark at startup, materialize up to it, then exit. Schedule with cron or a Kubernetes CronJob, the same model as `filedge run`.
- **`continuous`** — a long-running loop that cuts Micro-batches as the topic fills, stopping cleanly on SIGTERM after the in-flight batch is promoted and its offset committed.

```bash
# Drain a topic into complete NDJSON Files + manifests in ./landing/
filedge-materialize --config sources.yaml --source orders

# Preview the topic and target directory without consuming
filedge-materialize --config sources.yaml --source orders --dry-run

# Then ingest exactly like any file drop
filedge run --dir ./landing --config pipeline.yaml --audit-db-url $FILEDGE_AUDIT_DB_URL
```

The Kafka client lives behind an optional `kafka` extra (`pip install filedge[kafka]`), so the core ingestion path and the Reference Fetcher take on no Kafka dependency. The broker-specific code sits behind a small consumer seam, so SQS or Kinesis is a new adapter plus Sources Config, not a rewrite of the staging, promotion, manifest, or batch logic. The Source Manifest emitter and the Fetch-Lock promotion are reusable building blocks (`filedge.companion`) shared with the Reference Fetcher.

### Sources Config

A Queue Source is a kafka-typed entry in the same `sources.yaml` the Reference Fetcher uses. Credentials (SASL/TLS) are named by reference (`env:NAME`) and resolved at consume time — never written into the file.

```yaml
version: 1
sources:
  - name: orders
    type: kafka
    brokers: [localhost:9092]      # or a comma-separated string
    topic: orders
    consumer_group: filedge-orders
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    batch_size: 1000               # count boundary
    batch_timeout_seconds: 30      # time boundary (whichever fires first)
    trigger: drain                 # or: continuous
    format: json
    gzip: false
    # security_protocol: SASL_SSL
    # sasl_mechanism: PLAIN
    # credentials:
    #   sasl_username: env:KAFKA_USER
    #   sasl_password: env:KAFKA_PASSWORD
```

The same example ships at `examples/sources.yaml`.

---

## Example: Kafka Orders -> S3 -> BigQuery

### 1. Materialize queue records to files

Configure Kafka Connect, Flink, Spark, or another materializer to write complete files to a staging prefix, then promote them to the Watched Directory:

```
s3://my-bucket/queue-staging/orders/
  orders.topic0.0000000100-0000000199.ndjson.tmp

s3://my-bucket/landing/orders/
  orders.topic0.0000000100-0000000199.ndjson
```

The exact materializer command or deployment is outside Filedge's contract. Filedge only requires complete, immutable files in the Watched Directory.

### 2. Ingest

```bash
filedge run \
  --dir s3://my-bucket/landing/orders/ \
  --config      pipeline.yaml \
  --audit-db-url $FILEDGE_AUDIT_DB_URL
```

### 3. Configure the file schema

`pipeline.yaml` describes the files that the materializer lands:

```yaml
format: ndjson

connector:
  type: bigquery
  project: my-gcp-project
  dataset: raw

destination_table: raw_orders
write_mode: append

columns:
  - source: order_id
    dest: order_id
    type: string
    required: true
  - source: customer_id
    dest: customer_id
    type: string
    required: true
  - source: amount
    dest: amount
    type: float
    required: true
  - source: created_at
    dest: created_at
    type: timestamp
    required: true
```

---

## Scheduling

Schedule or run the Queue Materializer according to queue latency needs. Then schedule `filedge run` against the materialized output prefix.

```
Always-running materializer:
  ├── queue-materializer writes complete files to s3://.../landing/orders/

Every 10 min:
  └── filedge run --dir s3://.../landing/orders/ --config pipeline.yaml ...
```

For lower latency, run `filedge run` more frequently or partition the Watched Directory by time so each run scans a bounded prefix.

---

## Responsibility Boundary

| Concern | Owner |
|---|---|
| Broker authentication | Queue Materializer |
| Consumer groups and offset commits | Queue Materializer |
| Rebalance handling | Queue Materializer |
| Message decoding | Queue Materializer |
| Schema Registry integration | Queue Materializer |
| Poison-message handling | Queue Materializer |
| Partial-file atomicity (staging -> Watched Directory) | Queue Materializer |
| File-level deduplication (Content Hash) | `filedge run` |
| PENDING -> COMMITTED state machine | `filedge run` |
| Row-level provenance (`_source_file_hash`) | `filedge run` |
| Retry on ingestion failure | `filedge run` |
| Operator visibility (`filedge status`) | `filedge run` |

---

## Source Manifests (optional)

Filename conventions (`{topic}.{partition}.{start_offset}-{end_offset}.ndjson`) make Files queryable but don't survive across renames or repartitioning. For durable lineage, write a `*.manifest.json` sidecar next to each NDJSON file:

```
landing/orders/
  orders.3.1450000-1455000.ndjson
  orders.3.1450000-1455000.ndjson.manifest.json
```

The sidecar records the full offset range as structured JSON, plus the Queue Materializer's run identity:

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-25T10:30:00Z",
  "producer": "https://github.com/apache/kafka-connect",
  "run": {
    "runId": "kc-orders-2026-05-25T10:00",
    "facets": {"_filedgeManifest": {"manifest_version": "1", "record_count": 5000}}
  },
  "job": {"namespace": "queue", "name": "kafka.orders"},
  "inputs": [{
    "name": "kafka://broker/orders",
    "facets": {"_sourceRange": {
      "topic": "orders",
      "partition": 3,
      "start_offset": 1450000,
      "end_offset": 1455000
    }}
  }]
}
```

Filedge stores `source_type`, `source_name`, `producer`, `external_run_id`, and the full source range on each Audit Record. Operators query with `filedge lineage <hash>` or use `filedge status --json` to route Kafka failures back to the materializer team.

See [Source Manifests](source-manifests.md) for the full schema, policy modes, and validation rules. The default `optional` policy preserves the existing filename-only workflow when manifests are absent.
