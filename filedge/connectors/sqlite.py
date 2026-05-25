import datetime
import sqlite3
from typing import Iterator, List, Optional

from filedge.cdc import apply_transactional_cdc
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import configured_columns, expected_columns, provenance_columns, schema_mismatches

def _q(name: str) -> str:
    """Double-quote a SQLite identifier, escaping embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


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
        mismatches = schema_mismatches(
            existing,
            expected_columns(config, _TYPE_TO_SQL, "INTEGER", "TEXT"),
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n" + "\n".join(mismatches)
            )

    def _create_table(self, conn: sqlite3.Connection, config: PipelineConfig) -> None:
        col_defs = ["_id INTEGER PRIMARY KEY"]
        for col in configured_columns(config, _TYPE_TO_SQL):
            col_defs.append(f"{_q(col.name)} {col.type}")
        for col in provenance_columns(_TYPE_TO_SQL, "TEXT"):
            col_defs.append(f"{_q(col.name)} {col.type} NOT NULL")
        conn.execute(f"CREATE TABLE {config.dest_table} ({', '.join(col_defs)})")
        conn.execute(
            f"CREATE INDEX {config.dest_table}_source_file_hash_idx"
            f" ON {config.dest_table} (_source_file_hash)"
        )

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
                    quoted = ", ".join(_q(c) for c in dest_cols)
                    insert_sql = (
                        f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})"
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

    def write_cdc_rows(
        self,
        table: str,
        rows: Iterator[dict],
        file_hash: str,
        cdc: CdcConfig,
    ) -> None:
        conn = self._get_conn()
        ingested_at = datetime.datetime.now(datetime.UTC).isoformat()
        try:
            apply_transactional_cdc(
                _SQLiteCdcAdapter(conn),
                table,
                rows,
                file_hash=file_hash,
                ingested_at=ingested_at,
                cdc=cdc,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def healthcheck(self) -> None:
        self._get_conn().execute("SELECT 1").fetchone()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class _SQLiteCdcAdapter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def delete_by_key(self, table, key_columns, key_values):
        predicate = " AND ".join(f"{_q(col)} = ?" for col in key_columns)
        self._conn.execute(
            f"DELETE FROM {table} WHERE {predicate}", list(key_values)
        )

    def insert_row(self, table, columns, values):
        quoted = ", ".join(_q(col) for col in columns)
        placeholders = ", ".join(["?"] * len(columns))
        self._conn.execute(
            f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})", list(values)
        )
