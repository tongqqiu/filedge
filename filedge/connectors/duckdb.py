import datetime
from typing import Dict, Iterator, List, Optional

from filedge.cdc import apply_transactional_cdc
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import configured_columns, expected_columns, provenance_columns, schema_mismatches

_TYPE_TO_SQL = {
    "string": "VARCHAR",
    "integer": "INTEGER",
    "float": "DOUBLE",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOLEAN",
}


class DuckDBConnector(Connector):
    def __init__(self, path: str, write_mode: str = "append", batch_size: int = 1000, **_):
        try:
            import duckdb
        except ImportError as e:
            raise ImportError(
                "DuckDB connector requires an optional dependency"
                " — run: pip install filedge[duckdb]"
            ) from e

        try:
            self._conn = duckdb.connect(path)
        except Exception as e:
            if "lock" in str(e).lower() or "database is locked" in str(e).lower():
                raise RuntimeError(
                    f"DuckDB file is locked by another process: {path}"
                ) from e
            raise

        self._write_mode = write_mode
        self._batch_size = batch_size

    def ensure_table(self, config: PipelineConfig) -> None:
        existing = self._get_existing_columns(config.dest_table)
        if existing is None:
            self._create_table(config)
            return
        mismatches = schema_mismatches(
            existing,
            expected_columns(config, _TYPE_TO_SQL, "INTEGER", "TIMESTAMP"),
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n" + "\n".join(mismatches)
            )

    def _get_existing_columns(self, table: str) -> Optional[Dict[str, str]]:
        try:
            result = self._conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = ?",
                [table],
            ).fetchall()
        except Exception:
            return None
        if not result:
            return None
        return {row[0]: row[1].upper() for row in result}

    def _create_table(self, config: PipelineConfig) -> None:
        self._conn.execute(
            f"CREATE SEQUENCE IF NOT EXISTS {config.dest_table}_id_seq"
        )
        col_defs = ", ".join(
            f"{col.name} {col.type}"
            for col in configured_columns(config, _TYPE_TO_SQL)
        )
        provenance_defs = ", ".join(
            f"{col.name} {col.type} NOT NULL"
            for col in provenance_columns(_TYPE_TO_SQL, "TIMESTAMP")
        )
        ddl = (
            f"CREATE TABLE {config.dest_table} ("
            f"_id INTEGER DEFAULT nextval('{config.dest_table}_id_seq') PRIMARY KEY, "
            + col_defs
            + ", "
            + provenance_defs
            + ")"
        )
        self._conn.execute(ddl)
        self._conn.execute(
            f"CREATE INDEX {config.dest_table}_source_file_hash_idx"
            f" ON {config.dest_table} (_source_file_hash)"
        )

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        import pyarrow as pa

        ingested_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()

        try:
            self._conn.begin()
            if self._write_mode == "truncate":
                self._conn.execute(f"DELETE FROM {table}")
            else:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE _source_file_hash = ?", [file_hash]
                )

            batch: List[dict] = []
            for row in rows:
                record = dict(row)
                record["_source_file_hash"] = file_hash
                record["_ingested_at"] = ingested_at
                batch.append(record)
                if len(batch) >= self._batch_size:
                    self._flush(table, batch, pa)
                    batch = []

            if batch:
                self._flush(table, batch, pa)

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _flush(self, table: str, batch: List[dict], pa) -> None:
        arrow_table = pa.Table.from_pylist(batch)
        cols = ", ".join(arrow_table.column_names)
        self._conn.register("_etl_batch", arrow_table)
        self._conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _etl_batch")
        self._conn.unregister("_etl_batch")

    def write_cdc_rows(
        self,
        table: str,
        rows: Iterator[dict],
        file_hash: str,
        cdc: CdcConfig,
    ) -> None:
        # CDC retries are idempotent without an applied-files marker: the orchestrator
        # collapses the file to one final change per key, and each apply is
        # "DELETE WHERE key = ?" followed (for insert/update) by an INSERT. Re-running
        # produces the same destination state. Cross-file ordering is operator-owned
        # (see CDC File Order in CONTEXT.md), the same contract as SQLite/Postgres CDC.
        ingested_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
        try:
            self._conn.begin()
            apply_transactional_cdc(
                _DuckDBCdcAdapter(self._conn),
                table,
                rows,
                file_hash=file_hash,
                ingested_at=ingested_at,
                cdc=cdc,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def healthcheck(self) -> None:
        self._conn.execute("SELECT 1").fetchone()

    def close(self) -> None:
        self._conn.close()


class _DuckDBCdcAdapter:
    def __init__(self, conn) -> None:
        self._conn = conn

    def delete_by_key(self, table, key_columns, key_values):
        predicate = " AND ".join(f"{col} = ?" for col in key_columns)
        self._conn.execute(
            f"DELETE FROM {table} WHERE {predicate}", list(key_values)
        )

    def insert_row(self, table, columns, values):
        cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        self._conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(values)
        )
