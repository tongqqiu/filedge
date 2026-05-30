# Iceberg is a Table Format ingested via a Materializer companion, not a Parser

Apache Iceberg is **not** added as a Parser format alongside CSV, NDJSON, Parquet, Excel, and fixed-width. If an Iceberg-originated dataset must reach a Filedge Destination, it is materialized to complete NDJSON Files by an upstream Iceberg Materializer — the same external-companion role as ADR-0006 (API sources) and ADR-0007 (Queue sources) — and `filedge run` ingests those Files unchanged.

This ADR exists because the natural prompt is real: a growing share of object-store sinks (Kafka → S3) now land **Iceberg tables** rather than loose Parquet/NDJSON objects, so "should Filedge read Iceberg?" will keep coming up. Recording the reasoning once keeps that question landing on the established boundary rather than re-litigating it.

## Iceberg is a different category from a Parser format

Filedge's ingestion boundary is the **File**: complete bytes in a Watched Directory, identified by a single Content Hash, run through the `PENDING → PROCESSING → COMMITTED/FAILED` state machine. CSV, NDJSON, Parquet, Excel, and fixed-width all satisfy that contract — each is one self-contained File whose bytes *are* the unit of work.

Iceberg is a **table format**, not a file format. An Iceberg table is a catalog pointer plus a snapshot composed of a manifest list, manifest files, many Parquet/ORC/Avro **data files**, and (for merge-on-read tables) positional and equality **delete files**. Reading "the table" means:

- resolving a **snapshot** through a catalog (AWS Glue, Hive Metastore, REST, Nessie) — Filedge has no catalog concept and no business owning one;
- walking manifest metadata to enumerate the live data files for that snapshot;
- applying delete files so logically-deleted rows do not reappear.

None of this fits "hash a File's bytes and ingest them." There is no single File to hash, and the unit of work is a transactional table version, not a File. Adding Iceberg is therefore not a new entry in the Parser seam (the trap ADR-0012 guards against) — it is a new dependency surface (catalog clients, snapshot isolation, delete reconciliation) of a fundamentally different shape.

### The "we already read Parquet" trap

Filedge can already read Parquet, so it could be pointed at the data files *underneath* an Iceberg table today. This is silently wrong: it ignores snapshot isolation (mixing files from multiple snapshots) and merge-on-read deletes (resurrecting rows the table considers deleted). Reading Iceberg correctly requires the table-format machinery above; reading its data files directly is not "partial Iceberg support," it is data corruption with extra steps.

## Iceberg already provides what Filedge would re-impose

The deeper reason to decline is that Iceberg solves, at the **table layer**, much of what Filedge provides at the **File layer**: ACID commits, snapshots, schema evolution, time travel, and an auditable history. Data that already lives in Iceberg is already in an audited, transactional, queryable analytical table. "Iceberg → another Destination via Filedge" is usually the *overkill* case — the idiomatic move is to query the Iceberg table directly (Trino, Spark, Athena, DuckDB's iceberg extension), not to re-ingest it through a File-reliability layer whose guarantees are largely redundant with the source.

## Decision

- Iceberg is **not** a Parser format and will not be read natively by the core ingestion path.
- The boundary is unchanged: an **Iceberg Materializer** (an external companion — Spark, Flink, a PyIceberg job, or a future first-party reference) reads a snapshot through the catalog, honors deletes, writes complete NDJSON Files, and emits the OpenLineage-shaped Source Manifest (ADR-0011) carrying snapshot id / sequence number as the source range. `filedge run` then applies Content Hash deduplication, Strict Mode validation, row-level provenance, and the audit state machine exactly as for any File.
- A first-party reference Iceberg Materializer is **not** committed here. It would follow ADR-0018's external-companion shape (separate entry point, zero core dependency, never a loader of record) — but only on the same evidence bar below.

## The evidence bar (ADR-0005/0006/0007/0012)

Scope follows real target-user evidence, not ecosystem popularity:

- *"Iceberg is everywhere; support it for completeness"* → **declined.** This is the abstract-completeness argument the project consistently rejects.
- *"A concrete target user lands Kafka in Iceberg and needs it loaded into a relational/operational Destination with Filedge's per-File audit guarantees, and querying Iceberg directly does not meet the need"* → then the answer is an **Iceberg Materializer companion**, never a core Parser — and even then, the direct-query alternative is confirmed inadequate first.

## Considered Options

- **Iceberg Materializer companion (chosen direction if demand arises).** Preserves the File boundary, reuses the Source Manifest and audit model, and keeps catalog/snapshot/delete complexity in tools built for it.
- **Native Iceberg Parser in the core.** Rejected: a category error against the File boundary, pulls catalog clients and snapshot/delete reconciliation into core, and re-implements guarantees Iceberg already provides.
- **Point Parquet support at Iceberg data files.** Rejected: silently incorrect (ignores snapshot isolation and merge-on-read deletes).
- **Iceberg as a Destination connector.** Out of scope for this ADR (it concerns *sources*), but a more natural fit than reading Iceberg — tracked separately if a target user needs to write *to* Iceberg.

## Consequences

- Filedge stays a File-ingestion reliability layer; it does not grow a catalog client or table-format engine.
- Operators with Iceberg sources have a clear, supported pattern (materialize to Files) and a clear signal that direct querying is often the better answer.
- A future first-party Iceberg Materializer has a defined shape (ADR-0018) and a defined bar (this ADR) to clear before it is built.
