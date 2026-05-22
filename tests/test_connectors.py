import pytest

from etl.config import ConnectorConfig, PipelineConfig, ColumnMapping
from etl.connectors import get_connector


@pytest.fixture
def base_config():
    return PipelineConfig(
        format="csv",
        dest_table="t",
        columns=[ColumnMapping(source="name", dest="name", type="string")],
    )


def test_registry_resolves_sqlite_from_audit_url(tmp_path, base_config):
    url = f"sqlite:///{tmp_path}/test.db"
    connector = get_connector(base_config, audit_db_url=url)
    assert type(connector).__name__ == "SQLiteConnector"
    connector.close()


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
    with pytest.raises(ImportError, match="pip install etl-big-idea\\[bigquery\\]"):
        get_connector(base_config)


def test_registry_raises_on_no_connector_and_unknown_url(base_config):
    with pytest.raises(ValueError):
        get_connector(base_config, audit_db_url="mysql://localhost/db")
