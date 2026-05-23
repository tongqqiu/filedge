import importlib
import sys
import types

import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors import SchemaError


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql):
        self.conn.statements.append(sql)
        if "information_schema.columns" in sql:
            self._result = self.conn.existing_columns
        else:
            self._result = []

    def fetchall(self):
        return self._result


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.existing_columns = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="orders",
        columns=[
            ColumnMapping(source="name", dest="name", type="string"),
            ColumnMapping(source="amount", dest="amount", type="float"),
            ColumnMapping(source="created", dest="created", type="date"),
        ],
    )


@pytest.fixture
def fake_databricks(monkeypatch):
    conn = FakeConnection()
    sql_module = types.SimpleNamespace(connect=lambda **_: conn)
    databricks_module = types.SimpleNamespace(sql=sql_module)
    monkeypatch.setitem(sys.modules, "databricks", databricks_module)
    monkeypatch.setitem(sys.modules, "databricks.sql", sql_module)
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    return conn


def _connector(tmp_path, fake_databricks, **kwargs):
    module = importlib.import_module("filedge.connectors.databricks")
    staging_location = kwargs.pop("staging_location", str(tmp_path))
    return module.DatabricksConnector(
        server_hostname="adb.example.databricks.com",
        http_path="/sql/1.0/warehouses/abc",
        catalog="main",
        schema="default",
        staging_location=staging_location,
        **kwargs,
    )


def test_ensure_table_creates_databricks_native_table(tmp_path, fake_databricks, config):
    connector = _connector(tmp_path, fake_databricks)

    connector.ensure_table(config)

    ddl = fake_databricks.statements[-1]
    assert "CREATE TABLE `main`.`default`.`orders`" in ddl
    assert "_id BIGINT GENERATED ALWAYS AS IDENTITY" in ddl
    assert "`name` STRING" in ddl
    assert "`amount` DOUBLE" in ddl
    assert "`created` DATE" in ddl
    assert "_source_file_hash STRING NOT NULL" in ddl
    assert "_ingested_at TIMESTAMP NOT NULL" in ddl


def test_ensure_table_is_idempotent_when_schema_matches(
    tmp_path, fake_databricks, config
):
    fake_databricks.existing_columns = [
        ("_id", "BIGINT"),
        ("name", "STRING"),
        ("amount", "DOUBLE"),
        ("created", "DATE"),
        ("_source_file_hash", "STRING"),
        ("_ingested_at", "TIMESTAMP"),
    ]
    connector = _connector(tmp_path, fake_databricks)

    connector.ensure_table(config)

    assert not any(stmt.startswith("CREATE TABLE") for stmt in fake_databricks.statements)


def test_ensure_table_raises_schema_error_on_mismatch(
    tmp_path, fake_databricks, config
):
    fake_databricks.existing_columns = [
        ("_id", "BIGINT"),
        ("name", "STRING"),
        ("amount", "STRING"),
        ("created", "DATE"),
        ("_source_file_hash", "STRING"),
        ("_ingested_at", "TIMESTAMP"),
    ]
    connector = _connector(tmp_path, fake_databricks)

    with pytest.raises(SchemaError, match="amount.*STRING.*DOUBLE"):
        connector.ensure_table(config)


def test_write_rows_append_uses_copy_into_and_merge(tmp_path, fake_databricks):
    fake_databricks.existing_columns = [
        ("_id", "BIGINT"),
        ("name", "STRING"),
        ("amount", "DOUBLE"),
        ("_source_file_hash", "STRING"),
        ("_ingested_at", "TIMESTAMP"),
    ]
    connector = _connector(tmp_path, fake_databricks)

    connector.write_rows(
        "orders",
        iter([{"name": "Alice", "amount": 10.0}, {"name": "Bob", "amount": 20.0}]),
        "hash1",
    )

    statements = "\n".join(fake_databricks.statements)
    assert "CREATE TABLE `main`.`default`.`_filedge_staging_" in statements
    assert "`name` STRING" in statements
    assert "`amount` DOUBLE" in statements
    assert "COPY INTO `main`.`default`.`_filedge_staging_" in statements
    assert "`) FROM" not in statements
    assert "MERGE INTO `main`.`default`.`orders` AS dest" in statements
    assert "ON dest._source_file_hash = staging._source_file_hash" in statements
    assert "WHEN NOT MATCHED THEN INSERT" in statements
    assert "DELETE FROM" not in statements
    assert "DROP TABLE IF EXISTS `main`.`default`.`_filedge_staging_" in statements


def test_write_rows_truncate_replaces_table_from_staging(tmp_path, fake_databricks):
    connector = _connector(tmp_path, fake_databricks, write_mode="truncate")

    connector.write_rows("orders", iter([{"name": "Alice"}]), "hash1")

    statements = "\n".join(fake_databricks.statements)
    assert "TRUNCATE TABLE `main`.`default`.`orders`" in statements
    assert "INSERT INTO `main`.`default`.`orders`" in statements
    assert "MERGE INTO" not in statements


def test_write_rows_uploads_volume_staging_file_with_files_api(
    tmp_path, fake_databricks, monkeypatch
):
    module = importlib.import_module("filedge.connectors.databricks")
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b""

    def fake_urlopen(request):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    connector = _connector(
        tmp_path,
        fake_databricks,
        staging_location="/Volumes/main/default/test/filedge-staging",
    )

    connector.write_rows("orders", iter([{"name": "Alice"}]), "hash1")

    methods_and_urls = [(r.get_method(), r.full_url) for r in requests]
    assert methods_and_urls[0] == (
        "PUT",
        "https://adb.example.databricks.com/api/2.0/fs/directories"
        "/Volumes/main/default/test/filedge-staging",
    )
    assert methods_and_urls[1][0] == "PUT"
    assert (
        "https://adb.example.databricks.com/api/2.0/fs/files"
        "/Volumes/main/default/test/filedge-staging/filedge_hash1_"
    ) in methods_and_urls[1][1]
    assert methods_and_urls[1][1].endswith(".json?overwrite=true")
    assert b'"name": "Alice"' in requests[1].data
    assert b'"_source_file_hash": "hash1"' in requests[1].data
    assert b'"_ingested_at": "' in requests[1].data
    assert methods_and_urls[2][0] == "DELETE"
    assert "/api/2.0/fs/files/Volumes/main/default/test/filedge-staging/" in (
        methods_and_urls[2][1]
    )
    assert "COPY INTO `main`.`default`.`_filedge_staging_" in "\n".join(
        fake_databricks.statements
    )
    assert "`) FROM" not in "\n".join(fake_databricks.statements)
    assert " FROM '/Volumes/main/default/test/filedge-staging/" in "\n".join(
        fake_databricks.statements
    )


def test_write_rows_requires_staging_location(fake_databricks, monkeypatch):
    module = importlib.import_module("filedge.connectors.databricks")
    monkeypatch.delenv("DATABRICKS_STAGING_LOCATION", raising=False)
    connector = module.DatabricksConnector(
        server_hostname="adb.example.databricks.com",
        http_path="/sql/1.0/warehouses/abc",
        catalog="main",
        schema="default",
    )

    with pytest.raises(ValueError, match="staging_location"):
        connector.write_rows("orders", iter([{"name": "Alice"}]), "hash1")


def test_missing_sdk_raises_import_error_with_hint(monkeypatch):
    module = importlib.import_module("filedge.connectors.databricks")
    monkeypatch.setitem(sys.modules, "databricks", None)
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")

    with pytest.raises(ImportError, match="pip install filedge\\[databricks\\]"):
        module.DatabricksConnector(
            server_hostname="adb.example.databricks.com",
            http_path="/sql/1.0/warehouses/abc",
            catalog="main",
            schema="default",
        )
