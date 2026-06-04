import sqlite3
from typing import Optional

from filedge.connectors.sql_base import SqlConnector, SqliteDialect


class SQLiteConnector(SqlConnector):
    """Load Files into a SQLite table with content-hash idempotency.

    Connection setup only — the write algorithm lives in `SqlConnector` and the
    dialect deltas in `SqliteDialect` (see CONTEXT.md > SqlDialect, ADR-0022).
    """

    def __init__(self, url: str, write_mode: str = "append", batch_size: int = 1000, **_):
        self._path = url[len("sqlite:///"):]
        self._write_mode = write_mode
        self._batch_size = batch_size
        self._conn: Optional[sqlite3.Connection] = None
        self._dialect = SqliteDialect()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
        return self._conn

    def _get_conn(self) -> sqlite3.Connection:
        """Live SQLite connection (lazy). Retained as the connector's test seam."""
        return self._connection()
