import pytest

from filedge.config import ConnectorConfig, PipelineConfig, ColumnMapping
from filedge.connectors import (
    available_connector_types,
    connector_descriptor,
    get_connector,
)


@pytest.fixture
def base_config():
    return PipelineConfig(
        format="csv",
        dest_table="t",
        columns=[ColumnMapping(source="name", dest="name", type="string")],
    )


def test_registry_resolves_explicit_sqlite(tmp_path, base_config):
    url = f"sqlite:///{tmp_path}/test.db"
    base_config.connector = ConnectorConfig(type="sqlite", options={"url": url})
    connector = get_connector(base_config)
    assert type(connector).__name__ == "SQLiteConnector"
    connector.close()


def test_registry_raises_on_unknown_type(base_config):
    base_config.connector = ConnectorConfig(type="oracle", options={})
    with pytest.raises(ValueError, match="Unknown connector type 'oracle'"):
        get_connector(base_config)


def test_registry_raises_import_error_with_hint_for_missing_sdk(base_config):
    # bigquery extra not installed in test env
    base_config.connector = ConnectorConfig(type="bigquery", options={})
    with pytest.raises(ImportError, match="pip install filedge\\[bigquery\\]"):
        get_connector(base_config)


def test_registry_raises_import_error_with_hint_for_missing_databricks_sdk(
    base_config, monkeypatch
):
    import sys

    monkeypatch.setitem(sys.modules, "databricks", None)
    base_config.connector = ConnectorConfig(
        type="databricks",
        options={
            "server_hostname": "adb.example.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "catalog": "main",
            "schema": "default",
        },
    )
    with pytest.raises(ImportError, match="pip install filedge\\[databricks\\]"):
        get_connector(base_config)


def test_registry_raises_on_missing_connector_block(base_config):
    with pytest.raises(ValueError, match="connector: block"):
        get_connector(base_config)


def test_registry_exposes_authoring_metadata_without_loading_optional_sdks():
    assert available_connector_types() == [
        "bigquery",
        "databricks",
        "duckdb",
        "postgres",
        "snowflake",
        "sqlite",
    ]

    bigquery = connector_descriptor("bigquery")
    assert [setting.name for setting in bigquery.settings] == ["project", "dataset"]
    assert [p.env_var for p in bigquery.credential_placeholders] == [
        "GOOGLE_APPLICATION_CREDENTIALS"
    ]

    snowflake = connector_descriptor("snowflake")
    assert [setting.name for setting in snowflake.settings] == [
        "account", "user", "warehouse", "database", "schema", "role"
    ]
    assert [p.env_var for p in snowflake.credential_placeholders] == ["SNOWFLAKE_PASSWORD"]

    postgres = connector_descriptor("postgres")
    assert postgres.settings == ()
    assert [p.env_var for p in postgres.credential_placeholders] == ["DATABASE_URL"]
