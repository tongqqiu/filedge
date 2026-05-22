import datetime
from typing import Dict, Iterator, List, Optional

from etl.config import PipelineConfig
from etl.connectors import Connector
from etl.db import SchemaError

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
                " — run: pip install etl-big-idea[duckdb]"
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
        mismatches = self._detect_mismatches(existing, config)
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
        col_defs = ["_id INTEGER PRIMARY KEY"]
        for col in config.columns:
            col_defs.append(f"{col.dest} {_TYPE_TO_SQL.get(col.type, 'VARCHAR')}")
        col_defs.append("_source_file_hash VARCHAR NOT NULL")
        col_defs.append("_ingested_at TIMESTAMP NOT NULL")
        self._conn.execute(
            f"CREATE SEQUENCE IF NOT EXISTS {config.dest_table}_id_seq"
        )
        ddl = (
            f"CREATE TABLE {config.dest_table} ("
            f"_id INTEGER DEFAULT nextval('{config.dest_table}_id_seq') PRIMARY KEY, "
            + ", ".join(
                f"{col.dest} {_TYPE_TO_SQL.get(col.type, 'VARCHAR')}"
                for col in config.columns
            )
            + ", _source_file_hash VARCHAR NOT NULL"
            + ", _ingested_at TIMESTAMP NOT NULL"
            + ")"
        )
        self._conn.execute(ddl)

    def _detect_mismatches(self, existing: Dict[str, str], config: PipelineConfig) -> List[str]:
        required = {col.dest for col in config.columns} | {"_source_file_hash", "_ingested_at"}
        return [
            f"  Column '{name}' declared in pipeline.yaml but missing from table"
            for name in sorted(required)
            if name not in existing
        ]

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        ingested_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
        dest_cols: Optional[List[str]] = None

        try:
            self._conn.begin()
            if self._write_mode == "truncate":
                self._conn.execute(f"DELETE FROM {table}")
            else:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE _source_file_hash = ?", [file_hash]
                )

            batch = []
            for row in rows:
                if dest_cols is None:
                    dest_cols = list(row.keys()) + ["_source_file_hash", "_ingested_at"]
                    placeholders = ", ".join(["?"] * len(dest_cols))
                    insert_sql = (
                        f"INSERT INTO {table} ({', '.join(dest_cols)})"
                        f" VALUES ({placeholders})"
                    )
                batch.append(list(row.values()) + [file_hash, ingested_at])
                if len(batch) >= self._batch_size:
                    self._conn.executemany(insert_sql, batch)
                    batch = []

            if batch:
                self._conn.executemany(insert_sql, batch)

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()
