import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors.duckdb import DuckDBConnector
from filedge.connectors import SchemaError


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="orders",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="amount", dest="amount", type="float", required=True),
        ],
    )


@pytest.fixture
def connector(tmp_path, config):
    c = DuckDBConnector(path=str(tmp_path / "dest.duckdb"), write_mode="append", batch_size=100)
    yield c
    c.close()


def _row_count(connector, table):
    return connector._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_ensure_table_creates_table_with_provenance(connector, config):
    connector.ensure_table(config)
    cols = {
        row[0]
        for row in connector._conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'orders'"
        ).fetchall()
    }
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
    connector.write_rows("orders", iter(rows), "hash1")
    assert _row_count(connector, "orders") == 2


def test_write_rows_append_idempotent_for_same_hash(connector, config):
    connector.ensure_table(config)
    rows = [{"name": "Alice", "amount": 10.0}]
    connector.write_rows("orders", iter(rows), "hash1")
    connector.write_rows("orders", iter(rows), "hash1")  # retry — same hash
    assert _row_count(connector, "orders") == 1


def test_write_rows_append_accumulates_different_hashes(connector, config):
    connector.ensure_table(config)
    connector.write_rows("orders", iter([{"name": "Alice", "amount": 1.0}]), "hash1")
    connector.write_rows("orders", iter([{"name": "Bob", "amount": 2.0}]), "hash2")
    assert _row_count(connector, "orders") == 2


def test_write_rows_truncate_replaces_rows(tmp_path, config):
    tc = DuckDBConnector(path=str(tmp_path / "trunc.duckdb"), write_mode="truncate", batch_size=100)
    tc.ensure_table(config)
    tc.write_rows("orders", iter([{"name": "Alice", "amount": 1.0}]), "hash1")
    assert _row_count(tc, "orders") == 1
    tc.write_rows(
        "orders",
        iter([{"name": "Bob", "amount": 2.0}, {"name": "Carol", "amount": 3.0}]),
        "hash2",
    )
    assert _row_count(tc, "orders") == 2  # Alice gone, Bob+Carol present
    tc.close()


def test_write_rows_rollback_on_error(connector, config):
    connector.ensure_table(config)

    def bad_iter():
        yield {"name": "Alice", "amount": 1.0}
        raise ValueError("injected error")

    with pytest.raises(ValueError):
        connector.write_rows("orders", bad_iter(), "hash1")

    assert _row_count(connector, "orders") == 0


def test_provenance_columns_set_correctly(connector, config):
    connector.ensure_table(config)
    connector.write_rows("orders", iter([{"name": "Alice", "amount": 5.0}]), "myhash")
    row = connector._conn.execute(
        "SELECT _source_file_hash, _ingested_at FROM orders"
    ).fetchone()
    assert row[0] == "myhash"
    assert row[1] is not None


def test_native_duckdb_types(tmp_path):
    typed_config = PipelineConfig(
        format="csv",
        dest_table="typed",
        columns=[
            ColumnMapping(source="label", dest="label", type="string"),
            ColumnMapping(source="count", dest="count", type="integer"),
            ColumnMapping(source="score", dest="score", type="float"),
            ColumnMapping(source="active", dest="active", type="boolean"),
            ColumnMapping(source="created", dest="created", type="date"),
            ColumnMapping(source="ts", dest="ts", type="timestamp"),
        ],
    )
    tc = DuckDBConnector(path=str(tmp_path / "typed.duckdb"), write_mode="append", batch_size=100)
    tc.ensure_table(typed_config)
    types = {
        row[0]: row[1]
        for row in tc._conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'typed'"
        ).fetchall()
    }
    assert types["label"] == "VARCHAR"
    assert types["count"] == "INTEGER"
    assert types["score"] == "DOUBLE"
    assert types["active"] == "BOOLEAN"
    assert types["created"] == "DATE"
    assert types["ts"] == "TIMESTAMP"
    tc.close()


def test_missing_sdk_raises_import_error_with_hint(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(ImportError, match="pip install filedge\\[duckdb\\]"):
        DuckDBConnector(path=str(tmp_path / "x.duckdb"))
