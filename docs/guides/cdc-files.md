# Ingesting CDC Files

Filedge can apply complete change data capture (CDC) Files as SCD Type 1 changes. The source boundary stays the same: external tools such as Debezium, AWS DMS, Fivetran, Kafka Connect, or database export jobs write complete Files to a Watched Directory, then `filedge run` ingests them.

CDC support is a write mode, not a source connector.

```
CDC producer -> CDC Files -> Watched Directory -> filedge run -> Destination
```

!!! warning "File size limit"
    Filedge loads all rows from a CDC file into memory to resolve per-key ordering before writing. Keep individual CDC files under **~500,000 rows**. If your CDC producer emits larger files, split them before placing them in the watched directory.

---

## Example

Given an NDJSON File:

```json
{"customer_id":"c1","email":"old@example.com","updated_at":"2026-05-01T00:00:00","op":"c"}
{"customer_id":"c1","email":"new@example.com","updated_at":"2026-05-02T00:00:00","op":"u"}
{"customer_id":"c2","email":"gone@example.com","updated_at":"2026-05-03T00:00:00","op":"d"}
```

Configure the pipeline:

```yaml
format: ndjson
dest_table: customers
write_mode: cdc

connector:
  type: sqlite
  url: sqlite:///customers.db

cdc:
  keys: [customer_id]
  operation_column: op
  sequence_by: updated_at
  operations:
    insert: [c, insert]
    update: [u, update]
    delete: [d, delete]

columns:
  - source: customer_id
    dest: customer_id
    type: string
    required: true
  - source: email
    dest: email
    type: string
    required: false
  - source: updated_at
    dest: updated_at
    type: timestamp
    required: true
```

Run it like any other pipeline:

```bash
filedge run \
  --dir ./landing/customers \
  --config pipeline.yaml \
  --audit-db-url sqlite:///audit.db
```

---

## Semantics

For each CDC File, Filedge:

- parses and transforms rows using the normal `columns:` mapping
- normalizes operation values into insert, update, or delete
- keeps only the latest change per key within the File, based on `sequence_by`
- applies inserts and updates by replacing the current row for the key
- applies deletes by removing the current row for the key
- writes `_source_file_hash` and `_ingested_at` for inserted or updated rows
- marks the File COMMITTED only after the Connector applies the changes

If any row is invalid, the whole File fails under Strict Mode.

---

## File Order

CDC File order matters when multiple Files contain changes for the same key. Filedge processes Files in sorted path order during a Run. Your CDC producer or materializer must name or partition Files so that sorted path order matches the intended apply order:

```
customers_20260524T100000.ndjson
customers_20260524T101000.ndjson
customers_20260524T102000.ndjson
```

Within one File, `sequence_by` chooses the final change for a key. Across Files, Filedge does not infer a global order from row-level sequence values in the current SCD Type 1 model.

---


## Supported Connectors

CDC Files are currently supported by:

- SQLite
- PostgreSQL
- DuckDB
- BigQuery
- Databricks

### Retry safety

Each connector keeps CDC applies idempotent per Content Hash, but the mechanism
differs by destination class:

| Connector             | Mechanism                                                                  |
| --------------------- | -------------------------------------------------------------------------- |
| SQLite, PostgreSQL, DuckDB | In-transaction `DELETE` by business key followed by `INSERT` for non-deletes. Re-running the same File converges to the same destination state because `plan_cdc_changes` collapses each File to one final change per key. |
| BigQuery, Databricks  | An `_filedge_applied_files` marker table records each `(destination_table, content_hash)` that has been applied. The Connector skips the apply if the marker is already present. Required because staged-MERGE flows cannot be wrapped in a single audit transaction. |

DuckDB falls in the first group: the destination is a single local file with
full transactional DDL/DML, so a `BEGIN ... COMMIT` around the per-key DELETE
and INSERT statements is enough — no applied-files marker table is needed.

---

## Out Of Scope

Filedge does not capture database logs, run Debezium, consume Kafka topics, or manage replication slots. SCD Type 2 history tables are also out of scope for the first CDC implementation.
