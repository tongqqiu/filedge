import os
import uuid

import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors.databricks import DatabricksConnector

DATABRICKS_SERVER_HOSTNAME = os.environ.get("DATABRICKS_SERVER_HOSTNAME", "")
DATABRICKS_HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "")
DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG", "")
DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "")
DATABRICKS_STAGING_LOCATION = os.environ.get("DATABRICKS_STAGING_LOCATION", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("FILEDGE_DATABRICKS_INTEGRATION") != "1"
    or not os.environ.get("DATABRICKS_TOKEN")
    or not DATABRICKS_SERVER_HOSTNAME
    or not DATABRICKS_HTTP_PATH
    or not DATABRICKS_CATALOG
    or not DATABRICKS_SCHEMA
    or not DATABRICKS_STAGING_LOCATION,
    reason=(
        "Databricks integration tests require FILEDGE_DATABRICKS_INTEGRATION=1, "
        "DATABRICKS_TOKEN, DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, "
        "DATABRICKS_CATALOG, DATABRICKS_SCHEMA, and DATABRICKS_STAGING_LOCATION"
    ),
)


@pytest.fixture
def table_name():
    return f"filedge_it_orders_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def config(table_name):
    return PipelineConfig(
        format="csv",
        dest_table=table_name,
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="amount", dest="amount", type="float", required=True),
        ],
    )


@pytest.fixture
def connector(config):
    c = _connector()
    yield c
    _drop_table(c, config.dest_table)
    c.close()


def _connector(**kwargs):
    return DatabricksConnector(
        server_hostname=DATABRICKS_SERVER_HOSTNAME,
        http_path=DATABRICKS_HTTP_PATH,
        catalog=DATABRICKS_CATALOG,
        schema=DATABRICKS_SCHEMA,
        staging_location=DATABRICKS_STAGING_LOCATION,
        **kwargs,
    )


def _file_hash(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _drop_table(connector, table):
    with connector._conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {connector._table_ref(table)}")


def _row_count(connector, table):
    with connector._conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {connector._table_ref(table)}")
        return cur.fetchone()[0]


def _all_names(connector, table):
    with connector._conn.cursor() as cur:
        cur.execute(f"SELECT name FROM {connector._table_ref(table)} ORDER BY name")
        return [row[0] for row in cur.fetchall()]


def _column_types(connector, table):
    with connector._conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type"
            f" FROM {connector._quote(connector._catalog)}.information_schema.columns"
            f" WHERE table_schema = '{connector._string_literal(connector._schema)}'"
            f" AND table_name = '{connector._string_literal(table)}'"
        )
        return {row[0]: str(row[1]).upper() for row in cur.fetchall()}


def test_ensure_table_creates_table_with_provenance(connector, config):
    connector.ensure_table(config)

    cols = _column_types(connector, config.dest_table)

    assert "name" in cols
    assert "amount" in cols
    assert "_source_file_hash" in cols
    assert "_ingested_at" in cols


def test_ensure_table_is_idempotent(connector, config):
    connector.ensure_table(config)
    connector.ensure_table(config)


def test_native_databricks_types_on_destination_table(connector, config):
    typed_config = PipelineConfig(
        format="csv",
        dest_table=config.dest_table,
        columns=[
            ColumnMapping(source="label", dest="label", type="string"),
            ColumnMapping(source="count", dest="count", type="integer"),
            ColumnMapping(source="score", dest="score", type="float"),
            ColumnMapping(source="active", dest="active", type="boolean"),
            ColumnMapping(source="created", dest="created", type="date"),
            ColumnMapping(source="ts", dest="ts", type="timestamp"),
        ],
    )

    connector.ensure_table(typed_config)

    types = _column_types(connector, typed_config.dest_table)
    assert types["label"] == "STRING"
    assert types["count"] in {"BIGINT", "LONG"}
    assert types["score"] == "DOUBLE"
    assert types["active"] == "BOOLEAN"
    assert types["created"] == "DATE"
    assert types["ts"] == "TIMESTAMP"


def test_write_rows_append_idempotent_for_same_hash(connector, config):
    connector.ensure_table(config)
    rows = [{"name": "Alice", "amount": 10.0}]
    file_hash = _file_hash("retry")

    connector.write_rows(config.dest_table, iter(rows), file_hash)
    connector.write_rows(config.dest_table, iter(rows), file_hash)

    assert _row_count(connector, config.dest_table) == 1


def test_write_rows_append_accumulates_different_hashes(connector, config):
    connector.ensure_table(config)

    connector.write_rows(
        config.dest_table,
        iter([{"name": "Alice", "amount": 1.0}]),
        _file_hash("first"),
    )
    connector.write_rows(
        config.dest_table,
        iter([{"name": "Bob", "amount": 2.0}]),
        _file_hash("second"),
    )

    assert _row_count(connector, config.dest_table) == 2


def test_write_rows_truncate_replaces_rows(config):
    connector = _connector(write_mode="truncate")
    try:
        connector.ensure_table(config)
        connector.write_rows(
            config.dest_table,
            iter([{"name": "Alice", "amount": 1.0}]),
            _file_hash("truncate_first"),
        )
        assert _row_count(connector, config.dest_table) == 1

        connector.write_rows(
            config.dest_table,
            iter(
                [
                    {"name": "Bob", "amount": 2.0},
                    {"name": "Carol", "amount": 3.0},
                ]
            ),
            _file_hash("truncate_second"),
        )

        assert _row_count(connector, config.dest_table) == 2
        assert _all_names(connector, config.dest_table) == ["Bob", "Carol"]
    finally:
        _drop_table(connector, config.dest_table)
        connector.close()


def test_pipeline_smoke_loads_file_to_databricks(tmp_path, table_name):
    from filedge.pipeline import run_pipeline

    watched = tmp_path / "watch"
    watched.mkdir()
    source = watched / "orders.csv"
    source.write_text("name,amount\nAlice,10.5\nBob,20.25\n")

    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        f"format: csv\n"
        f"dest_table: {table_name}\n"
        f"batch_size: 100\n"
        f"connector:\n"
        f"  type: databricks\n"
        f"  server_hostname: {DATABRICKS_SERVER_HOSTNAME}\n"
        f"  http_path: {DATABRICKS_HTTP_PATH}\n"
        f"  catalog: {DATABRICKS_CATALOG}\n"
        f"  schema: {DATABRICKS_SCHEMA}\n"
        f"  staging_location: {DATABRICKS_STAGING_LOCATION}\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: amount\n    dest: amount\n    type: float\n    required: true\n"
    )
    audit_db_url = f"sqlite:///{tmp_path}/audit.db"
    connector = _connector()

    try:
        result = run_pipeline(str(watched), str(config_file), audit_db_url)

        assert result["committed"] == 1
        assert result["failed"] == 0
        assert _row_count(connector, table_name) == 2
    finally:
        _drop_table(connector, table_name)
        connector.close()
