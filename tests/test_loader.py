import pytest

from etl.config import ColumnMapping, PipelineConfig
from etl.connectors.sqlite import SQLiteConnector
from etl.loader import load_file


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="string", required=True),
        ],
        batch_size=2,
    )


@pytest.fixture
def connector(tmp_path, config):
    url = f"sqlite:///{tmp_path}/loader_test.db"
    c = SQLiteConnector(url=url, write_mode="append", batch_size=2)
    c.ensure_table(config)
    return c


def test_load_file_inserts_all_rows(connector, config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nfoo,bar\nbaz,qux\n")

    rows, error = load_file(connector, config, str(f), "testhash")
    assert error is None
    assert rows == 2

    conn = connector._get_conn()
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 2


def test_load_file_sets_provenance_columns(connector, config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nalpha,beta\n")

    load_file(connector, config, str(f), "myhash")

    conn = connector._get_conn()
    row = conn.execute("SELECT _source_file_hash, _ingested_at FROM items").fetchone()
    assert row[0] == "myhash"
    assert row[1] is not None


def test_load_file_returns_error_on_bad_row(connector, config, tmp_path):
    # Missing required column 'value' triggers TransformError → strict mode
    f = tmp_path / "bad.csv"
    f.write_text("name\nfoo\n")

    rows, error = load_file(connector, config, str(f), "badhash")
    assert error is not None
    assert "value" in error

    # Connector rolled back internally — no rows should be present
    conn = connector._get_conn()
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 0


def test_load_file_streams_across_batch_boundary(connector, config, tmp_path):
    # batch_size=2, load 5 rows — exercises multiple batch flushes
    lines = ["name,value"] + [f"row{i},{i}" for i in range(5)]
    f = tmp_path / "data.csv"
    f.write_text("\n".join(lines) + "\n")

    rows, error = load_file(connector, config, str(f), "batchhash")
    assert error is None
    assert rows == 5

    conn = connector._get_conn()
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 5
