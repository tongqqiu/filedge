import datetime
import sqlite3
from typing import Dict, Iterator, List, Optional

from filedge.config import PipelineConfig
from filedge.connectors import Connector, SchemaError

_TYPE_TO_SQL = {
    "string": "TEXT",
    "integer": "INTEGER",
    "float": "REAL",
    "date": "TEXT",
    "timestamp": "TEXT",
    "boolean": "INTEGER",
}


class SQLiteConnector(Connector):
    def __init__(self, url: str, write_mode: str = "append", batch_size: int = 1000, **_):
        self._path = url[len("sqlite:///"):]
        self._write_mode = write_mode
        self._batch_size = batch_size
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
        return self._conn

    def ensure_table(self, config: PipelineConfig) -> None:
        conn = self._get_conn()
        cursor = conn.execute(f"PRAGMA table_info({config.dest_table})")
        rows = cursor.fetchall()
        if not rows:
            self._create_table(conn, config)
            conn.commit()
            return
        existing = {row[1]: row[2].upper() for row in rows}
        mismatches = self._detect_mismatches(existing, config)
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n" + "\n".join(mismatches)
            )

    def _create_table(self, conn: sqlite3.Connection, config: PipelineConfig) -> None:
        col_defs = ["_id INTEGER PRIMARY KEY"]
        for col in config.columns:
            col_defs.append(f"{col.dest} {_TYPE_TO_SQL.get(col.type, 'TEXT')}")
        col_defs.append("_source_file_hash TEXT NOT NULL")
        col_defs.append("_ingested_at TEXT NOT NULL")
        conn.execute(f"CREATE TABLE {config.dest_table} ({', '.join(col_defs)})")
        conn.execute(
            f"CREATE INDEX {config.dest_table}_source_file_hash_idx"
            f" ON {config.dest_table} (_source_file_hash)"
        )

    def _detect_mismatches(self, existing: Dict[str, str], config: PipelineConfig) -> List[str]:
        required = {col.dest for col in config.columns} | {"_source_file_hash", "_ingested_at"}
        return [
            f"  Column '{name}' declared in pipeline.yaml but missing from table"
            for name in sorted(required)
            if name not in existing
        ]

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        conn = self._get_conn()
        dest_cols: Optional[List[str]] = None
        ingested_at = datetime.datetime.now(datetime.UTC).isoformat()

        try:
            if self._write_mode == "truncate":
                conn.execute(f"DELETE FROM {table}")
            else:
                conn.execute(
                    f"DELETE FROM {table} WHERE _source_file_hash = ?", [file_hash]
                )

            batch = []
            for row in rows:
                if dest_cols is None:
                    dest_cols = list(row.keys()) + ["_source_file_hash", "_ingested_at"]
                    placeholders = ", ".join(["?"] * len(dest_cols))
                    insert_sql = (
                        f"INSERT INTO {table} ({', '.join(dest_cols)}) VALUES ({placeholders})"
                    )
                values = list(row.values()) + [file_hash, ingested_at]
                batch.append(values)
                if len(batch) >= self._batch_size:
                    conn.executemany(insert_sql, batch)
                    batch = []

            if batch:
                conn.executemany(insert_sql, batch)

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
