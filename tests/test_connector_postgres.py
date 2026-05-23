import os
import uuid

import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors.postgres import PostgresConnector
from filedge.connectors import SchemaError

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="PostgreSQL integration tests require DATABASE_URL to be set",
)


@pytest.fixture
def config():
    # Unique table name per test run to avoid cross-test pollution
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
    c = PostgresConnector(url=DATABASE_URL, write_mode="append", batch_size=100)
    yield c
    # Drop the test table and close
    with c._conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {config.dest_table}")
    c._conn.commit()
    c.close()


def _row_count(connector, table):
    with connector._conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def test_ensure_table_creates_table_with_provenance(connector, config):
    connector.ensure_table(config)
    with connector._conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            [config.dest_table],
        )
        cols = {row[0] for row in cur.fetchall()}
    assert "name" in cols
    assert "amount" in cols
    assert "_source_file_hash" in cols
    assert "_ingested_at" in cols


def test_ensure_table_is_idempotent(connector, config):
    connector.ensure_table(config)
    connector.ensure_table(config)  # must not raise


def test_ensure_table_raises_schema_error_on_mismatch(connector, config):
    connector.ensure_table(config)
    config.columns.append(
        ColumnMapping(source="extra", dest="extra", type="string", required=True)
    )
    with pytest.raises(SchemaError, match="extra"):
        connector.ensure_table(config)


def test_write_rows_append_inserts_rows(connector, config):
    connector.ensure_table(config)
    rows = [{"name": "Alice", "amount": 10.0}, {"name": "Bob", "amount": 20.0}]
    connector.write_rows(config.dest_table, iter(rows), "hash1")
    assert _row_count(connector, config.dest_table) == 2


def test_write_rows_append_idempotent_for_same_hash(connector, config):
    connector.ensure_table(config)
    rows = [{"name": "Alice", "amount": 10.0}]
    connector.write_rows(config.dest_table, iter(rows), "hash1")
    connector.write_rows(config.dest_table, iter(rows), "hash1")  # retry — same hash
    assert _row_count(connector, config.dest_table) == 1


def test_write_rows_append_accumulates_different_hashes(connector, config):
    connector.ensure_table(config)
    connector.write_rows(config.dest_table, iter([{"name": "Alice", "amount": 1.0}]), "hash1")
    connector.write_rows(config.dest_table, iter([{"name": "Bob", "amount": 2.0}]), "hash2")
    assert _row_count(connector, config.dest_table) == 2


def test_write_rows_truncate_replaces_rows(config):
    tc = PostgresConnector(url=DATABASE_URL, write_mode="truncate", batch_size=100)
    try:
        tc.ensure_table(config)
        tc.write_rows(config.dest_table, iter([{"name": "Alice", "amount": 1.0}]), "hash1")
        assert _row_count(tc, config.dest_table) == 1
        tc.write_rows(
            config.dest_table,
            iter([{"name": "Bob", "amount": 2.0}, {"name": "Carol", "amount": 3.0}]),
            "hash2",
        )
        assert _row_count(tc, config.dest_table) == 2  # Alice gone, Bob+Carol present
    finally:
        with tc._conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {config.dest_table}")
        tc._conn.commit()
        tc.close()


def test_write_rows_rollback_on_error(connector, config):
    connector.ensure_table(config)

    def bad_iter():
        yield {"name": "Alice", "amount": 1.0}
        raise ValueError("injected error")

    with pytest.raises(ValueError):
        connector.write_rows(config.dest_table, bad_iter(), "hash1")

    assert _row_count(connector, config.dest_table) == 0


def test_provenance_columns_set_correctly(connector, config):
    connector.ensure_table(config)
    connector.write_rows(config.dest_table, iter([{"name": "Alice", "amount": 5.0}]), "myhash")
    with connector._conn.cursor() as cur:
        cur.execute(f"SELECT _source_file_hash, _ingested_at FROM {config.dest_table}")
        row = cur.fetchone()
    assert row[0] == "myhash"
    assert row[1] is not None


def test_native_postgres_types_on_destination_table(config):
    typed_config = PipelineConfig(
        format="csv",
        dest_table=f"typed_{uuid.uuid4().hex[:8]}",
        columns=[
            ColumnMapping(source="label", dest="label", type="string"),
            ColumnMapping(source="count", dest="count", type="integer"),
            ColumnMapping(source="score", dest="score", type="float"),
            ColumnMapping(source="active", dest="active", type="boolean"),
            ColumnMapping(source="created", dest="created", type="date"),
            ColumnMapping(source="ts", dest="ts", type="timestamp"),
        ],
    )
    tc = PostgresConnector(url=DATABASE_URL, write_mode="append", batch_size=100)
    try:
        tc.ensure_table(typed_config)
        with tc._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = %s ORDER BY ordinal_position",
                [typed_config.dest_table],
            )
            types = {row[0]: row[1] for row in cur.fetchall()}
        assert types["count"] == "integer"
        assert types["score"] == "double precision"
        assert types["active"] == "boolean"
        assert types["created"] == "date"
        assert "timestamp" in types["ts"]
    finally:
        with tc._conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {typed_config.dest_table}")
        tc._conn.commit()
        tc.close()
