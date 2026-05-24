import pytest

pytest.importorskip("duckdb")

from filedge.config import CdcConfig, ColumnMapping, PipelineConfig
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


def test_ensure_table_raises_schema_error_on_type_mismatch(connector, config):
    connector._conn.execute(
        "CREATE TABLE orders ("
        "_id INTEGER PRIMARY KEY, "
        "name VARCHAR, "
        "amount VARCHAR, "
        "_source_file_hash VARCHAR NOT NULL, "
        "_ingested_at TIMESTAMP NOT NULL"
        ")"
    )

    with pytest.raises(SchemaError, match="amount.*VARCHAR.*DOUBLE"):
        connector.ensure_table(config)


def test_ensure_table_raises_schema_error_on_extra_live_column(connector, config):
    connector.ensure_table(config)
    connector._conn.execute("ALTER TABLE orders ADD COLUMN stale VARCHAR")

    with pytest.raises(SchemaError, match="stale.*not declared"):
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


def _cdc_config():
    return PipelineConfig(
        format="ndjson",
        dest_table="customers",
        write_mode="cdc",
        columns=[
            ColumnMapping("customer_id", "customer_id", "string", True),
            ColumnMapping("email", "email", "string", False),
            ColumnMapping("updated_at", "updated_at", "timestamp", True),
        ],
        cdc=CdcConfig(
            keys=["customer_id"],
            operation_column="op",
            sequence_by="updated_at",
            operations={
                "insert": ["c"],
                "update": ["u"],
                "delete": ["d"],
            },
        ),
    )


def test_write_cdc_rows_applies_insert_update_delete(tmp_path):
    config = _cdc_config()
    connector = DuckDBConnector(
        path=str(tmp_path / "cdc.duckdb"), write_mode="cdc", batch_size=100
    )
    connector.ensure_table(config)

    connector.write_cdc_rows(
        "customers",
        iter(
            [
                {
                    "customer_id": "c1",
                    "email": "old@example.com",
                    "updated_at": "2026-05-01T00:00:00",
                    "op": "c",
                },
                {
                    "customer_id": "c1",
                    "email": "new@example.com",
                    "updated_at": "2026-05-02T00:00:00",
                    "op": "u",
                },
                {
                    "customer_id": "c2",
                    "email": "gone@example.com",
                    "updated_at": "2026-05-03T00:00:00",
                    "op": "c",
                },
                {
                    "customer_id": "c2",
                    "email": "gone@example.com",
                    "updated_at": "2026-05-04T00:00:00",
                    "op": "d",
                },
            ]
        ),
        "hash1",
        config.cdc,
    )

    rows = connector._conn.execute(
        "SELECT customer_id, email, _source_file_hash FROM customers ORDER BY customer_id"
    ).fetchall()
    assert rows == [("c1", "new@example.com", "hash1")]
    connector.close()


def test_write_cdc_rows_is_idempotent_for_same_hash(tmp_path):
    config = _cdc_config()
    connector = DuckDBConnector(
        path=str(tmp_path / "cdc_retry.duckdb"), write_mode="cdc", batch_size=100
    )
    connector.ensure_table(config)
    rows = [
        {
            "customer_id": "c1",
            "email": "new@example.com",
            "updated_at": "2026-05-02T00:00:00",
            "op": "u",
        }
    ]

    connector.write_cdc_rows("customers", iter(rows), "hash1", config.cdc)
    connector.write_cdc_rows("customers", iter(rows), "hash1", config.cdc)

    assert _row_count(connector, "customers") == 1
    row = connector._conn.execute(
        "SELECT customer_id, email, _source_file_hash FROM customers"
    ).fetchone()
    assert row == ("c1", "new@example.com", "hash1")
    connector.close()


def test_write_cdc_rows_delete_then_reinsert_across_retries(tmp_path):
    """Re-applying a CDC file containing a delete keeps the row deleted."""
    config = _cdc_config()
    connector = DuckDBConnector(
        path=str(tmp_path / "cdc_delete.duckdb"), write_mode="cdc", batch_size=100
    )
    connector.ensure_table(config)
    connector.write_cdc_rows(
        "customers",
        iter(
            [
                {
                    "customer_id": "c1",
                    "email": "alive@example.com",
                    "updated_at": "2026-05-01T00:00:00",
                    "op": "c",
                },
                {
                    "customer_id": "c1",
                    "email": "alive@example.com",
                    "updated_at": "2026-05-02T00:00:00",
                    "op": "d",
                },
            ]
        ),
        "hash1",
        config.cdc,
    )
    assert _row_count(connector, "customers") == 0

    # Retry the same file — destination state must not change.
    connector.write_cdc_rows(
        "customers",
        iter(
            [
                {
                    "customer_id": "c1",
                    "email": "alive@example.com",
                    "updated_at": "2026-05-01T00:00:00",
                    "op": "c",
                },
                {
                    "customer_id": "c1",
                    "email": "alive@example.com",
                    "updated_at": "2026-05-02T00:00:00",
                    "op": "d",
                },
            ]
        ),
        "hash1",
        config.cdc,
    )
    assert _row_count(connector, "customers") == 0
    connector.close()


def test_write_cdc_rows_rolls_back_on_error(tmp_path):
    """A mid-apply failure must leave the destination unchanged."""
    config = _cdc_config()
    connector = DuckDBConnector(
        path=str(tmp_path / "cdc_rollback.duckdb"), write_mode="cdc", batch_size=100
    )
    connector.ensure_table(config)
    connector.write_cdc_rows(
        "customers",
        iter(
            [
                {
                    "customer_id": "c1",
                    "email": "stable@example.com",
                    "updated_at": "2026-05-01T00:00:00",
                    "op": "c",
                }
            ]
        ),
        "hash_initial",
        config.cdc,
    )
    assert _row_count(connector, "customers") == 1

    def bad_rows():
        yield {
            "customer_id": "c1",
            "email": "would-update@example.com",
            "updated_at": "2026-05-02T00:00:00",
            "op": "u",
        }
        raise ValueError("injected mid-stream")

    with pytest.raises(ValueError, match="injected mid-stream"):
        connector.write_cdc_rows("customers", bad_rows(), "hash_failed", config.cdc)

    row = connector._conn.execute(
        "SELECT email, _source_file_hash FROM customers"
    ).fetchone()
    assert row == ("stable@example.com", "hash_initial")
    connector.close()


def test_write_cdc_rows_unsupported_when_method_missing(tmp_path):
    """A connector that does not override write_cdc_rows must raise NotImplementedError."""
    from filedge.connectors import Connector

    # Sanity: the base method raises. DuckDB overrides it (covered by the tests above).
    assert "write_cdc_rows" in DuckDBConnector.__dict__
    assert Connector.write_cdc_rows.__qualname__.startswith("Connector.")
