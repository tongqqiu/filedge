import datetime
from typing import Iterator, List, Optional

from filedge.config import PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import expected_columns, schema_mismatches

_TYPE_TO_SQL = {
    "string": "TEXT",
    "integer": "INTEGER",
    "float": "DOUBLE PRECISION",
    "date": "DATE",
    "timestamp": "TIMESTAMP WITH TIME ZONE",
    "boolean": "BOOLEAN",
}


class PostgresConnector(Connector):
    def __init__(self, url: str, write_mode: str = "append", batch_size: int = 1000, **_):
        try:
            import psycopg2
        except ImportError as e:
            raise ImportError(
                "PostgreSQL connector requires an optional dependency"
                " — run: pip install filedge[postgres]"
            ) from e
        self._conn = psycopg2.connect(url)
        self._write_mode = write_mode
        self._batch_size = batch_size

    def ensure_table(self, config: PipelineConfig) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = %s",
                [config.dest_table],
            )
            rows = cur.fetchall()
        if not rows:
            self._create_table(config)
            self._conn.commit()
            return
        existing = {row[0]: row[1] for row in rows}
        mismatches = schema_mismatches(
            existing,
            expected_columns(config, _TYPE_TO_SQL, "BIGINT", "TIMESTAMP WITH TIME ZONE"),
            type_aliases={
                "BIGSERIAL": "BIGINT",
                "BIGINT": "BIGINT",
                "DOUBLE PRECISION": "DOUBLE PRECISION",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMP WITH TIME ZONE",
            },
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n" + "\n".join(mismatches)
            )

    def _create_table(self, config: PipelineConfig) -> None:
        col_defs = ["_id BIGSERIAL PRIMARY KEY"]
        for col in config.columns:
            col_defs.append(f"{col.dest} {_TYPE_TO_SQL.get(col.type, 'TEXT')}")
        col_defs.append("_source_file_hash TEXT NOT NULL")
        col_defs.append("_ingested_at TIMESTAMP WITH TIME ZONE NOT NULL")
        ddl = f"CREATE TABLE {config.dest_table} ({', '.join(col_defs)})"
        with self._conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute(
                f"CREATE INDEX {config.dest_table}_source_file_hash_idx"
                f" ON {config.dest_table} (_source_file_hash)"
            )

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        dest_cols: Optional[List[str]] = None
        ingested_at = datetime.datetime.now(datetime.UTC).isoformat()

        try:
            with self._conn.cursor() as cur:
                if self._write_mode == "truncate":
                    cur.execute(f"TRUNCATE TABLE {table}")
                else:
                    cur.execute(
                        f"DELETE FROM {table} WHERE _source_file_hash = %s", [file_hash]
                    )

                batch = []
                for row in rows:
                    if dest_cols is None:
                        dest_cols = list(row.keys()) + ["_source_file_hash", "_ingested_at"]
                        placeholders = ", ".join(["%s"] * len(dest_cols))
                        insert_sql = (
                            f"INSERT INTO {table} ({', '.join(dest_cols)})"
                            f" VALUES ({placeholders})"
                        )
                    values = list(row.values()) + [file_hash, ingested_at]
                    batch.append(values)
                    if len(batch) >= self._batch_size:
                        cur.executemany(insert_sql, batch)
                        batch = []

                if batch:
                    cur.executemany(insert_sql, batch)

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()
