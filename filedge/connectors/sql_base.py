"""Deep base for the cursor-`executemany` SQL Connectors.

`SqlConnector` owns the one write algorithm shared by `sqlite`, `postgres`, and
`snowflake`: `ensure_table` schema-diff, the idempotent
`DELETE WHERE _source_file_hash` + batched `INSERT`, and transactional SCD Type 1
CDC. Each backend supplies a thin `SqlDialect` carrying only the deltas (type
map, quoting, placeholder, identity DDL, truncate verb, `_ingested_at` literal,
index policy, and the schema-introspection query). Connection setup and
credentials stay in the concrete Connector — the part that legitimately differs
per backend. See CONTEXT.md > SqlDialect and ADR-0022.

DuckDB (Arrow bulk path) and the warehouse Connectors (BigQuery, Databricks)
deliberately do not use this base — their write mechanisms are genuine
variation, not duplication to collapse.
"""

import datetime
from typing import Dict, Iterator, List, Optional, Sequence

from filedge.cdc import apply_transactional_cdc
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import (
    configured_columns,
    expected_columns,
    provenance_columns,
    schema_mismatches,
)


def _double_quote(name: str) -> str:
    """Double-quote an identifier, escaping embedded quotes (ANSI / SQLite / Snowflake)."""
    return '"' + name.replace('"', '""') + '"'


class SqlDialect:
    """The per-Destination data a `SqlConnector` needs (see CONTEXT.md > SqlDialect).

    Subclasses set the data attributes and, where a backend genuinely differs,
    override `quote_table`, `truncate_sql`, or `now_literal`. `introspect_columns`
    is the one behavioural hook every dialect must implement: it returns the live
    table's ``{column: type}`` map, or ``None`` when the table does not yet exist.
    """

    type_map: Dict[str, str]
    placeholder: str
    identity_column_ddl: str
    id_type: str
    ingested_at_type: str
    type_aliases: Optional[Dict[str, str]] = None
    creates_index: bool = True

    def quote(self, name: str) -> str:
        return _double_quote(name)

    def quote_table(self, table: str) -> str:
        """How a table name appears in DML/DDL. Bare by default; Snowflake quotes."""
        return table

    def truncate_sql(self, qtable: str) -> str:
        return f"TRUNCATE TABLE {qtable}"

    def now_literal(self) -> str:
        return datetime.datetime.now(datetime.UTC).isoformat()

    def introspect_columns(self, cursor, table: str) -> Optional[Dict[str, str]]:
        raise NotImplementedError


class _SqlCdcAdapter:
    """One `TransactionalCdcAdapter` for every SQL dialect.

    Reads quoting and placeholder style off the `SqlDialect`, replacing the four
    near-identical `_*CdcAdapter` classes the connectors used to each define.
    """

    def __init__(self, cursor, dialect: SqlDialect) -> None:
        self._cursor = cursor
        self._dialect = dialect

    def delete_by_key(self, table: str, key_columns: Sequence[str], key_values: Sequence) -> None:
        d = self._dialect
        predicate = " AND ".join(f"{d.quote(col)} = {d.placeholder}" for col in key_columns)
        self._cursor.execute(
            f"DELETE FROM {d.quote_table(table)} WHERE {predicate}", list(key_values)
        )

    def insert_row(self, table: str, columns: Sequence[str], values: Sequence) -> None:
        d = self._dialect
        quoted = ", ".join(d.quote(col) for col in columns)
        placeholders = ", ".join([d.placeholder] * len(columns))
        self._cursor.execute(
            f"INSERT INTO {d.quote_table(table)} ({quoted}) VALUES ({placeholders})",
            list(values),
        )


class SqlConnector(Connector):
    """Shared implementation for cursor-`executemany` SQL Connectors.

    Subclasses set `self._conn`, `self._dialect`, `self._write_mode`, and
    `self._batch_size` in their constructor (and may override `_connection` for
    lazy connection acquisition).
    """

    _dialect: SqlDialect
    _conn = None
    _write_mode: str = "append"
    _batch_size: int = 1000

    def _connection(self):
        return self._conn

    def ensure_table(self, config: PipelineConfig) -> None:
        d = self._dialect
        conn = self._connection()
        cur = conn.cursor()
        existing = d.introspect_columns(cur, config.dest_table)
        if existing is None:
            self._create_table(cur, config)
            conn.commit()
            return
        mismatches = schema_mismatches(
            existing,
            expected_columns(config, d.type_map, d.id_type, d.ingested_at_type),
            type_aliases=d.type_aliases,
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n" + "\n".join(mismatches)
            )

    def _create_table(self, cursor, config: PipelineConfig) -> None:
        d = self._dialect
        col_defs = [d.identity_column_ddl]
        for col in configured_columns(config, d.type_map):
            col_defs.append(f"{d.quote(col.name)} {col.type}")
        for col in provenance_columns(d.type_map, d.ingested_at_type):
            col_defs.append(f"{d.quote(col.name)} {col.type} NOT NULL")
        qtable = d.quote_table(config.dest_table)
        cursor.execute(f"CREATE TABLE {qtable} ({', '.join(col_defs)})")
        if d.creates_index:
            cursor.execute(
                f"CREATE INDEX {config.dest_table}_source_file_hash_idx"
                f" ON {qtable} ({d.quote('_source_file_hash')})"
            )

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        d = self._dialect
        qtable = d.quote_table(table)
        ingested_at = d.now_literal()
        conn = self._connection()
        cur = conn.cursor()
        dest_cols: Optional[List[str]] = None
        insert_sql = ""
        try:
            if self._write_mode == "truncate":
                cur.execute(d.truncate_sql(qtable))
            else:
                cur.execute(
                    f"DELETE FROM {qtable} WHERE {d.quote('_source_file_hash')} = {d.placeholder}",
                    [file_hash],
                )

            batch: List[list] = []
            for row in rows:
                if dest_cols is None:
                    dest_cols = list(row.keys()) + ["_source_file_hash", "_ingested_at"]
                    placeholders = ", ".join([d.placeholder] * len(dest_cols))
                    quoted = ", ".join(d.quote(c) for c in dest_cols)
                    insert_sql = f"INSERT INTO {qtable} ({quoted}) VALUES ({placeholders})"
                batch.append(list(row.values()) + [file_hash, ingested_at])
                if len(batch) >= self._batch_size:
                    cur.executemany(insert_sql, batch)
                    batch = []

            if batch:
                cur.executemany(insert_sql, batch)

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
        ingested_at = self._dialect.now_literal()
        conn = self._connection()
        cur = conn.cursor()
        try:
            apply_transactional_cdc(
                _SqlCdcAdapter(cur, self._dialect),
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
        cur = self._connection().cursor()
        cur.execute("SELECT 1")
        cur.fetchone()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class SqliteDialect(SqlDialect):
    type_map = {
        "string": "TEXT",
        "integer": "INTEGER",
        "float": "REAL",
        "date": "TEXT",
        "timestamp": "TEXT",
        "boolean": "INTEGER",
    }
    placeholder = "?"
    identity_column_ddl = "_id INTEGER PRIMARY KEY"
    id_type = "INTEGER"
    ingested_at_type = "TEXT"

    def truncate_sql(self, qtable: str) -> str:
        return f"DELETE FROM {qtable}"

    def introspect_columns(self, cursor, table: str) -> Optional[Dict[str, str]]:
        rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            return None
        return {row[1]: row[2].upper() for row in rows}


class PostgresDialect(SqlDialect):
    type_map = {
        "string": "TEXT",
        "integer": "INTEGER",
        "float": "DOUBLE PRECISION",
        "date": "DATE",
        "timestamp": "TIMESTAMP WITH TIME ZONE",
        "boolean": "BOOLEAN",
    }
    placeholder = "%s"
    identity_column_ddl = "_id BIGSERIAL PRIMARY KEY"
    id_type = "BIGINT"
    ingested_at_type = "TIMESTAMP WITH TIME ZONE"
    type_aliases = {
        "BIGSERIAL": "BIGINT",
        "BIGINT": "BIGINT",
        "DOUBLE PRECISION": "DOUBLE PRECISION",
        "TIMESTAMP WITH TIME ZONE": "TIMESTAMP WITH TIME ZONE",
    }

    def introspect_columns(self, cursor, table: str) -> Optional[Dict[str, str]]:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = %s",
            [table],
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        return {row[0]: row[1] for row in rows}


class SnowflakeDialect(SqlDialect):
    type_map = {
        "string": "STRING",
        "integer": "NUMBER",
        "float": "FLOAT",
        "date": "DATE",
        "timestamp": "TIMESTAMP_NTZ",
        "boolean": "BOOLEAN",
    }
    placeholder = "%s"
    identity_column_ddl = '"_id" NUMBER AUTOINCREMENT'
    id_type = "NUMBER"
    ingested_at_type = "TIMESTAMP_NTZ"
    type_aliases = {
        "STRING": "TEXT",
        "VARCHAR": "TEXT",
        "INTEGER": "NUMBER",
        "INT": "NUMBER",
        "BIGINT": "NUMBER",
        "DOUBLE": "FLOAT",
        "FLOAT8": "FLOAT",
        "TIMESTAMP": "TIMESTAMP_NTZ",
    }
    creates_index = False

    def __init__(self, schema: str) -> None:
        self.schema = schema

    def quote_table(self, table: str) -> str:
        return self.quote(table)

    def now_literal(self) -> str:
        return (
            datetime.datetime.now(datetime.UTC)
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S.%f")
        )

    def introspect_columns(self, cursor, table: str) -> Optional[Dict[str, str]]:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            [self.schema, table],
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        return {row[0]: row[1] for row in rows}
