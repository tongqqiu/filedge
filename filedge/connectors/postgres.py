import os
from typing import Optional

from filedge.connectors.sql_base import PostgresDialect, SqlConnector


class PostgresConnector(SqlConnector):
    """Load Files into a PostgreSQL table with content-hash idempotency.

    Connection setup only — the write algorithm lives in `SqlConnector` and the
    dialect deltas in `PostgresDialect` (see CONTEXT.md > SqlDialect, ADR-0022).
    """

    def __init__(
        self,
        url: Optional[str] = None,
        write_mode: str = "append",
        batch_size: int = 1000,
        **_,
    ):
        try:
            import psycopg2
        except ImportError as e:
            raise ImportError(
                "PostgreSQL connector requires an optional dependency"
                " — run: pip install filedge[postgres]"
            ) from e
        url = url or os.environ.get("DATABASE_URL")
        if not url:
            raise ValueError("PostgresConnector requires DATABASE_URL to be set")
        self._conn = psycopg2.connect(url)
        self._write_mode = write_mode
        self._batch_size = batch_size
        self._dialect = PostgresDialect()
