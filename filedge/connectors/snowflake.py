import datetime
import os
from typing import Iterator, List, Optional

from filedge.cdc import apply_transactional_cdc
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import (
    configured_columns,
    expected_columns,
    provenance_columns,
    schema_mismatches,
)


def _q(name: str) -> str:
    """Double-quote a Snowflake identifier, escaping embedded quotes.

    Every identifier is quoted so names are stored case-sensitively exactly as
    written in pipeline.yaml (e.g. `order_id`, `_source_file_hash`) rather than
    folded to upper case by Snowflake's unquoted-identifier rule.
    """
    return '"' + name.replace('"', '""') + '"'


# DDL types Filedge writes. Snowflake's INFORMATION_SCHEMA reports these back
# under canonical names (STRING->TEXT, NUMBER->NUMBER, TIMESTAMP_NTZ->TIMESTAMP_NTZ),
# reconciled via _TYPE_ALIASES in ensure_table.
_TYPE_TO_SF = {
    "string": "STRING",
    "integer": "NUMBER",
    "float": "FLOAT",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ",
    "boolean": "BOOLEAN",
}

_TYPE_ALIASES = {
    "STRING": "TEXT",
    "VARCHAR": "TEXT",
    "INTEGER": "NUMBER",
    "INT": "NUMBER",
    "BIGINT": "NUMBER",
    "DOUBLE": "FLOAT",
    "FLOAT8": "FLOAT",
    "TIMESTAMP": "TIMESTAMP_NTZ",
}


class SnowflakeConnector(Connector):
    """Load Files into a Snowflake table with content-hash idempotency.

    Mirrors the PostgreSQL connector's model: a `DELETE WHERE _source_file_hash`
    followed by a batched `INSERT` inside one transaction makes re-loading the
    same File a no-op. CDC files apply row-by-row in a transaction. The secret is
    never read from pipeline.yaml — the password comes from `SNOWFLAKE_PASSWORD`.
    """

    def __init__(
        self,
        account: Optional[str] = None,
        user: Optional[str] = None,
        warehouse: Optional[str] = None,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        role: Optional[str] = None,
        write_mode: str = "append",
        batch_size: int = 1000,
        **_,
    ):
        try:
            import snowflake.connector
        except ImportError as e:
            raise ImportError(
                "Snowflake connector requires an optional dependency"
                " — run: pip install filedge[snowflake]"
            ) from e

        missing = [
            name
            for name, value in {
                "account": account,
                "user": user,
                "warehouse": warehouse,
                "database": database,
                "schema": schema,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                "SnowflakeConnector requires these connector fields: "
                + ", ".join(missing)
            )

        connect_kwargs = dict(
            account=account,
            user=user,
            warehouse=warehouse,
            database=database,
            schema=schema,
            autocommit=False,
        )
        if role:
            connect_kwargs["role"] = role
        self._apply_credentials(connect_kwargs)
        self._conn = snowflake.connector.connect(**connect_kwargs)
        self._schema = str(schema)
        self._write_mode = write_mode
        self._batch_size = batch_size

    @staticmethod
    def _apply_credentials(connect_kwargs: dict) -> None:
        """Resolve Snowflake auth from the environment into connect kwargs.

        Key-pair (RSA) auth is preferred and is the only programmatic option on
        Snowflake accounts where single-factor password sign-in is disabled: set
        `SNOWFLAKE_PRIVATE_KEY_PATH` to a PEM private-key file (and
        `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` if it is encrypted). `SNOWFLAKE_PASSWORD`
        remains a fallback. The key is never read from pipeline.yaml.
        """
        private_key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
        password = os.environ.get("SNOWFLAKE_PASSWORD")
        if private_key_path:
            connect_kwargs["private_key_file"] = private_key_path
            passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
            if passphrase:
                connect_kwargs["private_key_file_pwd"] = passphrase
        elif password:
            connect_kwargs["password"] = password
        else:
            raise ValueError(
                "SnowflakeConnector requires SNOWFLAKE_PRIVATE_KEY_PATH (key-pair "
                "auth, recommended) or SNOWFLAKE_PASSWORD to be set"
            )

    def ensure_table(self, config: PipelineConfig) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = %s",
                [self._schema, config.dest_table],
            )
            rows = cur.fetchall()
        if not rows:
            self._create_table(config)
            return
        existing = {row[0]: row[1] for row in rows}
        mismatches = schema_mismatches(
            existing,
            expected_columns(config, _TYPE_TO_SF, "NUMBER", "TIMESTAMP_NTZ"),
            type_aliases=_TYPE_ALIASES,
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n"
                + "\n".join(mismatches)
            )

    def _create_table(self, config: PipelineConfig) -> None:
        col_defs = ['"_id" NUMBER AUTOINCREMENT']
        for col in configured_columns(config, _TYPE_TO_SF):
            col_defs.append(f"{_q(col.name)} {col.type}")
        for col in provenance_columns(_TYPE_TO_SF, "TIMESTAMP_NTZ"):
            col_defs.append(f"{_q(col.name)} {col.type} NOT NULL")
        ddl = f"CREATE TABLE {_q(config.dest_table)} ({', '.join(col_defs)})"
        with self._conn.cursor() as cur:
            cur.execute(ddl)
        # DDL is auto-committed by Snowflake; commit() is a harmless no-op here.
        self._conn.commit()

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        dest_cols: Optional[List[str]] = None
        insert_sql = ""
        ingested_at = (
            datetime.datetime.now(datetime.UTC)
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S.%f")
        )

        try:
            with self._conn.cursor() as cur:
                if self._write_mode == "truncate":
                    cur.execute(f"TRUNCATE TABLE {_q(table)}")
                else:
                    cur.execute(
                        f"DELETE FROM {_q(table)} WHERE {_q('_source_file_hash')} = %s",
                        [file_hash],
                    )

                batch = []
                for row in rows:
                    if dest_cols is None:
                        dest_cols = list(row.keys()) + ["_source_file_hash", "_ingested_at"]
                        placeholders = ", ".join(["%s"] * len(dest_cols))
                        quoted = ", ".join(_q(c) for c in dest_cols)
                        insert_sql = (
                            f"INSERT INTO {_q(table)} ({quoted}) VALUES ({placeholders})"
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

    def write_cdc_rows(
        self,
        table: str,
        rows: Iterator[dict],
        file_hash: str,
        cdc: CdcConfig,
    ) -> None:
        ingested_at = (
            datetime.datetime.now(datetime.UTC)
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S.%f")
        )
        try:
            with self._conn.cursor() as cur:
                apply_transactional_cdc(
                    _SnowflakeCdcAdapter(cur),
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
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def close(self) -> None:
        self._conn.close()


class _SnowflakeCdcAdapter:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def delete_by_key(self, table, key_columns, key_values):
        predicate = " AND ".join(f"{_q(col)} = %s" for col in key_columns)
        self._cursor.execute(
            f"DELETE FROM {_q(table)} WHERE {predicate}", list(key_values)
        )

    def insert_row(self, table, columns, values):
        quoted = ", ".join(_q(col) for col in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        self._cursor.execute(
            f"INSERT INTO {_q(table)} ({quoted}) VALUES ({placeholders})", list(values)
        )
