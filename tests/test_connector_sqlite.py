import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors.sqlite import SQLiteConnector
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
    url = f"sqlite:///{tmp_path}/dest.db"
    c = SQLiteConnector(url=url, write_mode="append", batch_size=100)
    yield c
    c.close()


def _row_count(connector, table):
    return connector._get_conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _all_rows(connector, table):
    return connector._get_conn().execute(f"SELECT * FROM {table}").fetchall()


def test_ensure_table_creates_table_with_provenance(connector, config):
    connector.ensure_table(config)
    cols = {
        row[1]
        for row in connector._get_conn().execute("PRAGMA table_info(orders)").fetchall()
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
    conn = connector._get_conn()
    conn.execute(
        "CREATE TABLE orders ("
        "_id INTEGER PRIMARY KEY, "
        "name TEXT, "
        "amount TEXT, "
        "_source_file_hash TEXT NOT NULL, "
        "_ingested_at TEXT NOT NULL"
        ")"
    )
    conn.commit()

    with pytest.raises(SchemaError, match="amount.*TEXT.*REAL"):
        connector.ensure_table(config)


def test_ensure_table_raises_schema_error_on_extra_live_column(connector, config):
    connector.ensure_table(config)
    connector._get_conn().execute("ALTER TABLE orders ADD COLUMN stale TEXT")
    connector._get_conn().commit()

    with pytest.raises(SchemaError, match="stale.*not declared"):
        connector.ensure_table(config)


def test_quoted_reserved_identifiers_are_supported(tmp_path):
    config = PipelineConfig(
        format="csv",
        dest_table="select",
        columns=[
            ColumnMapping(source="from", dest="from", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="integer", required=True),
        ],
    )
    connector = SQLiteConnector(url=f"sqlite:///{tmp_path}/quoted.db", write_mode="append")

    connector.ensure_table(config)
    connector.write_rows("select", iter([{"from": "source", "value": 1}]), "hash1")

    row = connector._get_conn().execute('SELECT "from", "value" FROM "select"').fetchone()
    assert row == ("source", 1)
    connector.close()


def test_invalid_destination_identifier_is_rejected(connector):
    config = PipelineConfig(
        format="csv",
        dest_table="bad-table",
        columns=[ColumnMapping(source="name", dest="name", type="string")],
    )

    with pytest.raises(ValueError, match="Invalid destination table identifier"):
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


def test_write_rows_truncate_replaces_rows(connector, config):
    url = f"sqlite:///{pytest.importorskip('tempfile').mkdtemp()}/trunc.db"
    tc = SQLiteConnector(url=url, write_mode="truncate", batch_size=100)
    tc.ensure_table(config)
    tc.write_rows("orders", iter([{"name": "Alice", "amount": 1.0}]), "hash1")
    assert _row_count(tc, "orders") == 1
    tc.write_rows("orders", iter([{"name": "Bob", "amount": 2.0}, {"name": "Carol", "amount": 3.0}]), "hash2")
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
    row = connector._get_conn().execute(
        "SELECT _source_file_hash, _ingested_at FROM orders"
    ).fetchone()
    assert row[0] == "myhash"
    assert row[1] is not None
