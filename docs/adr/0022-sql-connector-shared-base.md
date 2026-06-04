# SQL Connectors share a `SqlConnector` base via `SqlDialect`; DuckDB and the warehouse Connectors stay separate

The `sqlite`, `postgres`, and `snowflake` Connectors run one algorithm — `ensure_table` schema-diff, the idempotent `DELETE WHERE _source_file_hash` + batched `INSERT`, and transactional SCD Type 1 CDC — and differ only in data: type map, identifier quoting, parameter placeholder, identity-column DDL, the truncate verb, the `_ingested_at` literal format, whether a secondary index is created, and the schema-introspection query. That algorithm is consolidated into a deep `SqlConnector` executed over a DB-API cursor, with each backend supplying a thin `SqlDialect` value object for the deltas. Connection setup and credentials stay in the concrete Connector, because that is the part that legitimately differs per backend (psycopg2 vs sqlite3 vs the Snowflake key-pair auth dance).

The non-obvious part is the **boundary**: not every SQL-speaking Connector belongs behind this seam. This ADR records *which Connectors are deliberately left out and why*, so the decision is not re-litigated each time the codebase is reviewed.

## Decision

- `SqlConnector` owns the shared write algorithm; `SqlDialect` carries the per-Destination data. `sqlite`, `postgres`, and `snowflake` are expressed this way. The four previously-duplicated `_*CdcAdapter` classes collapse into one that reads quoting and placeholder off the dialect.
- The **DuckDB Connector stays a standalone Connector**. Its write path is a different mechanism — a PyArrow table `register`'d and bulk-loaded via `INSERT … SELECT` — not a cursor `executemany`. Forcing it behind the seam would add a behavioral hook used by exactly one adapter, which is a hypothetical seam, not a real one. The Arrow path is genuine variation, not duplication to collapse.
- The **warehouse Connectors (`bigquery`, `databricks`) stay separate**. They write via NDJSON/cloud staging plus a native bulk-load or `MERGE`, and achieve idempotency through job IDs or Applied File Markers rather than `DELETE WHERE _source_file_hash`. They share neither the algorithm nor the cursor execution model.

## Considered Options

- **Fold all SQL-capable Connectors onto one base (rejected).** Pulling DuckDB in requires the base to abstract over the row-insert mechanism (cursor `executemany` vs Arrow bulk load) behind a hook only DuckDB would override; pulling BigQuery/Databricks in requires abstracting over the entire idempotency model. Both make the "deep" base shallower and the "thin" dialect fatter — the opposite of the win. The seam is drawn at *Connectors that share the cursor `executemany` algorithm*, and only those.

## Consequences

- The `Connector` interface (ADR-0004) is unchanged; the existing per-Connector tests cross it untouched and remain the regression net. The shared algorithm is exercised cheaply through the in-memory SQLite adapter; postgres and snowflake integration tests demote to per-dialect conformance.
- Adding a new cursor-`executemany` Destination means writing a `SqlDialect` plus connection setup, not a full Connector. Adding a Destination with a different write mechanism (another Arrow/columnar bulk loader, another warehouse) is still a standalone Connector — and that is the correct signal that it does not belong behind this seam.
