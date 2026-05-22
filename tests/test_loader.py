import pytest

from etl.config import ColumnMapping, PipelineConfig
from etl.db import ensure_destination_table
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
def db_with_table(db, config):
    ensure_destination_table(db, config)
    db.commit()
    return db


def test_load_file_inserts_all_rows(db_with_table, config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nfoo,bar\nbaz,qux\n")

    rows, error = load_file(db_with_table, config, str(f), "testhash")
    assert error is None
    assert rows == 2

    db_with_table.commit()
    cursor = db_with_table.execute("SELECT COUNT(*) FROM items")
    assert cursor.fetchone()[0] == 2


def test_load_file_sets_provenance_columns(db_with_table, config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nalpha,beta\n")

    load_file(db_with_table, config, str(f), "myhash")
    db_with_table.commit()

    cursor = db_with_table.execute("SELECT _source_file_hash, _ingested_at FROM items")
    row = cursor.fetchone()
    assert row[0] == "myhash"
    assert row[1] is not None


def test_load_file_rollback_leaves_no_rows(db_with_table, config, tmp_path):
    # Missing required column 'value' triggers TransformError → strict mode
    f = tmp_path / "bad.csv"
    f.write_text("name\nfoo\n")

    rows, error = load_file(db_with_table, config, str(f), "badhash")
    assert error is not None
    assert "value" in error

    db_with_table.rollback()

    cursor = db_with_table.execute("SELECT COUNT(*) FROM items")
    assert cursor.fetchone()[0] == 0


def test_load_file_streams_across_batch_boundary(db_with_table, config, tmp_path):
    # batch_size=2, load 5 rows — exercises multiple batch flushes
    lines = ["name,value"] + [f"row{i},{i}" for i in range(5)]
    f = tmp_path / "data.csv"
    f.write_text("\n".join(lines) + "\n")

    rows, error = load_file(db_with_table, config, str(f), "batchhash")
    assert error is None
    assert rows == 5

    db_with_table.commit()
    cursor = db_with_table.execute("SELECT COUNT(*) FROM items")
    assert cursor.fetchone()[0] == 5
