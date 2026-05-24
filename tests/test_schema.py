from filedge.config import ColumnMapping, PipelineConfig
from filedge.schema import (
    INGESTED_AT_COLUMN,
    SOURCE_FILE_HASH_COLUMN,
    configured_columns,
    expected_columns,
    provenance_columns,
)


def _config():
    return PipelineConfig(
        format="csv",
        dest_table="orders",
        columns=[
            ColumnMapping("order_id", "order_id", "integer", True),
            ColumnMapping("amount", "amount", "float", True),
        ],
    )


def test_configured_columns_project_pipeline_columns_to_backend_types():
    columns = configured_columns(
        _config(),
        {"string": "TEXT", "integer": "INTEGER", "float": "REAL"},
    )

    assert [(col.name, col.type) for col in columns] == [
        ("order_id", "INTEGER"),
        ("amount", "REAL"),
    ]


def test_provenance_columns_project_shared_destination_shape():
    columns = provenance_columns(
        {"string": "TEXT"},
        ingested_at_type="TIMESTAMP",
    )

    assert [(col.name, col.type) for col in columns] == [
        (SOURCE_FILE_HASH_COLUMN, "TEXT"),
        (INGESTED_AT_COLUMN, "TIMESTAMP"),
    ]


def test_expected_columns_combines_id_configured_and_provenance_columns():
    columns = expected_columns(
        _config(),
        {"string": "TEXT", "integer": "INTEGER", "float": "REAL"},
        id_type="BIGINT",
        ingested_at_type="TIMESTAMP",
    )

    assert [(col.name, col.type) for col in columns] == [
        ("_id", "BIGINT"),
        ("order_id", "INTEGER"),
        ("amount", "REAL"),
        (SOURCE_FILE_HASH_COLUMN, "TEXT"),
        (INGESTED_AT_COLUMN, "TIMESTAMP"),
    ]
