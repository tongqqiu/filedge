import sys
import types

from filedge.config import ColumnMapping, PipelineConfig
from filedge.connectors.bigquery import BigQueryConnector


class FakeField:
    def __init__(self, name, field_type):
        self.name = name
        self.field_type = field_type


class FakeTable:
    def __init__(self, schema):
        self.schema = schema


class FakeBigQueryClient:
    def get_table(self, _):
        return FakeTable(
            [
                FakeField("_id", "INTEGER"),
                FakeField("name", "STRING"),
                FakeField("amount", "FLOAT"),
                FakeField("_source_file_hash", "STRING"),
                FakeField("_ingested_at", "TIMESTAMP"),
            ]
        )


def test_ensure_table_accepts_bigquery_metadata_type_aliases(monkeypatch):
    google_module = types.ModuleType("google")
    cloud_module = types.ModuleType("google.cloud")
    api_core_module = types.ModuleType("google.api_core")
    cloud_module.bigquery = types.SimpleNamespace()
    exceptions_module = types.ModuleType("google.api_core.exceptions")
    exceptions_module.NotFound = type("NotFound", (Exception,), {})

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core_module)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions_module)

    connector = BigQueryConnector.__new__(BigQueryConnector)
    connector._bq = FakeBigQueryClient()
    connector._table_ref = lambda table: table
    config = PipelineConfig(
        format="csv",
        dest_table="orders",
        columns=[
            ColumnMapping(source="name", dest="name", type="string"),
            ColumnMapping(source="amount", dest="amount", type="float"),
        ],
    )

    connector.ensure_table(config)
