"""End-to-end Snowflake connector tests against a real account.

Gated: runs only with FILEDGE_SNOWFLAKE_INTEGRATION=1 and the SNOWFLAKE_* env
set (see .github/workflows/snowflake-integration.yml). The unit tests in
test_connector_snowflake.py cover SQL generation without a warehouse.
"""

import os
import uuid

import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors import SchemaError

_REQUIRED = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
]
_HAS_CREDENTIAL = bool(
    os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH") or os.environ.get("SNOWFLAKE_PASSWORD")
)
pytestmark = pytest.mark.skipif(
    os.environ.get("FILEDGE_SNOWFLAKE_INTEGRATION") != "1"
    or any(not os.environ.get(k) for k in _REQUIRED)
    or not _HAS_CREDENTIAL,
    reason=(
        "Snowflake integration tests require FILEDGE_SNOWFLAKE_INTEGRATION=1, "
        "SNOWFLAKE_ACCOUNT/USER/WAREHOUSE/DATABASE/SCHEMA, and a credential "
        "(SNOWFLAKE_PRIVATE_KEY_PATH for key-pair auth, or SNOWFLAKE_PASSWORD)"
    ),
)


def _settings():
    return dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        role=os.environ.get("SNOWFLAKE_ROLE"),
    )


@pytest.fixture
def config():
    table = f"orders_{uuid.uuid4().hex[:8]}"
    return PipelineConfig(
        format="csv",
        dest_table=table,
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="amount", dest="amount", type="float", required=True),
        ],
    )


@pytest.fixture
def connector(config):
    from filedge.connectors.snowflake import SnowflakeConnector

    c = SnowflakeConnector(**_settings(), write_mode="append", batch_size=100)
    yield c
    with c._conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{config.dest_table}"')
    c._conn.commit()
    c.close()


def _count(connector, table):
    with connector._conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]


def test_ensure_table_creates_table_with_provenance(connector, config):
    connector.ensure_table(config)
    with connector._conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            [_settings()["schema"], config.dest_table],
        )
        cols = {row[0] for row in cur.fetchall()}
    assert {"name", "amount", "_source_file_hash", "_ingested_at"} <= cols


def test_write_rows_is_idempotent_by_content_hash(connector, config):
    connector.ensure_table(config)
    rows = [{"name": "Alice", "amount": 1.0}, {"name": "Bob", "amount": 2.0}]

    connector.write_rows(config.dest_table, iter(rows), "hash-1")
    connector.write_rows(config.dest_table, iter(rows), "hash-1")  # re-load same hash

    assert _count(connector, config.dest_table) == 2  # not duplicated


def test_schema_mismatch_is_detected(connector, config):
    connector.ensure_table(config)
    drifted = PipelineConfig(
        format="csv",
        dest_table=config.dest_table,
        columns=[
            ColumnMapping(source="name", dest="name", type="integer"),  # was string
            ColumnMapping(source="amount", dest="amount", type="float"),
        ],
    )
    with pytest.raises(SchemaError):
        connector.ensure_table(drifted)


def test_healthcheck_round_trips(connector):
    connector.healthcheck()
