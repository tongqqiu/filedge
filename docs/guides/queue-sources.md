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
  --watched-dir s3://my-bucket/landing/orders/ \
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
  └── filedge run --watched-dir s3://.../landing/orders/ --config pipeline.yaml ...
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
