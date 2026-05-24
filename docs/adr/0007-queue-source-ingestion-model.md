# Queue Sources are materialized as Files before ingestion

Filedge does not consume directly from Kafka, SQS, Kinesis, or other message brokers. Its ingestion boundary is the **File**: complete bytes in a Watched Directory, identified by Content Hash and processed through the audit state machine.

Queue data must first be materialized as complete NDJSON or Parquet files by an upstream Queue Materializer. That materializer may be Kafka Connect S3 Sink, Kafka Connect GCS Sink, Flink, Spark Structured Streaming, Vector, Benthos, a cloud-native delivery service, or a custom consumer. Once the file exists, `filedge run` ingests it exactly like any other file drop.

The rejected alternative is making Filedge a native queue consumer. That would require owning consumer groups, offset commits, rebalance handling, backpressure, process lifetime, poison-message behavior, message decoding, schema registry integration, and broker-specific integration tests. Those concerns are real and important, but they belong in a queue materialization layer. Pulling them into Filedge would blur the product boundary and make the tool compete with established streaming infrastructure instead of strengthening its reliability layer.

By requiring Queue Sources to become Files first, queue-originated data receives the same Content Hash deduplication, strict validation, row-level provenance, retry behavior, and `filedge status` visibility as API exports, SFTP-landed files, and direct file drops. The Queue Materializer may include topic, partition, and offset range in filenames, object metadata, or sidecar manifests for operator traceability. Filedge still treats the materialized file bytes as the atomic unit of work.

## Considered Options

- **External Queue Materializer writes files, Filedge ingests files**: keeps Filedge focused on reliable file ingestion while letting mature queue tools own consumer semantics.
- **Native `filedge consume` with Drain trigger**: preserves a scheduled operational model, but still requires Kafka/SQS/Kinesis client dependencies, offset state, rebalance handling, poison-message policy, and long-running-process edge cases.
- **Native continuous consumer**: lower latency, but turns Filedge into an always-on service requiring process management, liveness checks, backpressure handling, and shutdown semantics.
- **Destination-direct queue sinks**: simple for some warehouses, but bypasses Filedge's uniform audit, Content Hash deduplication, strict validation, and row-level provenance model.
