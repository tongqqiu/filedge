import os
import uuid

import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors import SchemaError
from filedge.connectors.bigquery import BigQueryConnector

BIGQUERY_PROJECT = os.environ.get("BIGQUERY_PROJECT", "")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("FILEDGE_BIGQUERY_INTEGRATION") != "1"
    or not BIGQUERY_PROJECT
    or not BIGQUERY_DATASET,
    reason=(
        "BigQuery integration tests require FILEDGE_BIGQUERY_INTEGRATION=1, "
        "BIGQUERY_PROJECT, and BIGQUERY_DATASET"
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
    c = BigQueryConnector(
        project=BIGQUERY_PROJECT,
        dataset=BIGQUERY_DATASET,
        write_mode="append",
        batch_size=100,
    )
    yield c
    c._bq.delete_table(c._table_ref(config.dest_table), not_found_ok=True)
    c.close()


def _file_hash(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _row_count(connector, table):
    query = (
        f"SELECT COUNT(*) AS count "
        f"FROM `{BIGQUERY_PROJECT}.{BIGQUERY_DATASET}.{table}`"
    )
    rows = list(connector._bq.query(query).result())
    return rows[0]["count"]


def _all_rows(connector, table, columns):
    selected = ", ".join(columns)
    query = (
        f"SELECT {selected} "
        f"FROM `{BIGQUERY_PROJECT}.{BIGQUERY_DATASET}.{table}` "
        f"ORDER BY {selected}"
    )
    return [dict(row) for row in connector._bq.query(query).result()]


def test_ensure_table_creates_table_with_provenance(connector, config):
    connector.ensure_table(config)

    table = connector._bq.get_table(connector._table_ref(config.dest_table))
    cols = {field.name for field in table.schema}

    assert "name" in cols
    assert "amount" in cols
    assert "_source_file_hash" in cols
    assert "_ingested_at" in cols


def test_ensure_table_is_idempotent(connector, config):
    connector.ensure_table(config)
    connector.ensure_table(config)


def test_ensure_table_raises_schema_error_on_mismatch(connector, config):
    connector.ensure_table(config)
    config.columns.append(
        ColumnMapping(source="extra", dest="extra", type="string", required=True)
    )

    with pytest.raises(SchemaError, match="extra"):
        connector.ensure_table(config)


def test_native_bigquery_types_on_destination_table(connector, config):
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

    table = connector._bq.get_table(connector._table_ref(typed_config.dest_table))
    types = {field.name: field.field_type for field in table.schema}
    assert types["label"] == "STRING"
    assert types["count"] == "INTEGER"
    assert types["score"] == "FLOAT"
    assert types["active"] == "BOOLEAN"
    assert types["created"] == "DATE"
    assert types["ts"] == "TIMESTAMP"


def test_write_rows_append_inserts_rows(connector, config):
    connector.ensure_table(config)
    rows = [{"name": "Alice", "amount": 10.0}, {"name": "Bob", "amount": 20.0}]

    connector.write_rows(config.dest_table, iter(rows), _file_hash("insert"))

    assert _row_count(connector, config.dest_table) == 2


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
    connector = BigQueryConnector(
        project=BIGQUERY_PROJECT,
        dataset=BIGQUERY_DATASET,
        write_mode="truncate",
        batch_size=100,
    )
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
        assert _all_rows(connector, config.dest_table, ["name"]) == [
            {"name": "Bob"},
            {"name": "Carol"},
        ]
    finally:
        connector._bq.delete_table(
            connector._table_ref(config.dest_table), not_found_ok=True
        )
        connector.close()


def test_pipeline_smoke_loads_file_to_bigquery(tmp_path, table_name):
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
        f"  type: bigquery\n"
        f"  project: {BIGQUERY_PROJECT}\n"
        f"  dataset: {BIGQUERY_DATASET}\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: amount\n    dest: amount\n    type: float\n    required: true\n"
    )
    audit_db_url = f"sqlite:///{tmp_path}/audit.db"
    connector = BigQueryConnector(project=BIGQUERY_PROJECT, dataset=BIGQUERY_DATASET)

    try:
        result = run_pipeline(str(watched), str(config_file), audit_db_url)

        assert result["committed"] == 1
        assert result["failed"] == 0
        assert _row_count(connector, table_name) == 2
    finally:
        connector._bq.delete_table(connector._table_ref(table_name), not_found_ok=True)
        connector.close()
