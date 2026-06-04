import os
from typing import Optional

from filedge.connectors.sql_base import SnowflakeDialect, SqlConnector


class SnowflakeConnector(SqlConnector):
    """Load Files into a Snowflake table with content-hash idempotency.

    Connection setup and key-pair/password auth live here — the genuinely
    backend-specific part. The write algorithm lives in `SqlConnector` and the
    dialect deltas in `SnowflakeDialect` (see CONTEXT.md > SqlDialect, ADR-0022).
    A `DELETE WHERE _source_file_hash` followed by a batched `INSERT` inside one
    transaction makes re-loading the same File a no-op. The secret is never read
    from pipeline.yaml — it comes from the environment.
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
        self._write_mode = write_mode
        self._batch_size = batch_size
        self._dialect = SnowflakeDialect(str(schema))

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
