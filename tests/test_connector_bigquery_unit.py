import sys
import types

from filedge.config import CdcConfig, ColumnMapping, PipelineConfig
from filedge.connectors.bigquery import BigQueryConnector


class FakeField:
    def __init__(self, name, field_type):
        self.name = name
        self.field_type = field_type


class FakeTable:
    def __init__(self, schema):
        self.schema = schema


class FakeQueryJob:
    def result(self):
        return []


class FakeLoadJob:
    def result(self):
        return []


class FakeBigQueryClient:
    def __init__(self):
        self.queries = []
        self.load_jobs = []
        self.created_tables = []

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

    def query(self, sql):
        self.queries.append(sql)
        return FakeQueryJob()

    def load_table_from_file(self, file_obj, table_ref, job_config=None, job_id=None):
        self.load_jobs.append(
            {
                "table_ref": table_ref,
                "job_config": job_config,
                "job_id": job_id,
                "data": file_obj.read(),
            }
        )
        return FakeLoadJob()

    def create_table(self, table):
        self.created_tables.append(table)


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


def test_write_cdc_rows_uses_marker_staging_and_transaction(monkeypatch):
    _install_fake_bigquery_modules(monkeypatch)
    connector = BigQueryConnector.__new__(BigQueryConnector)
    connector._bq = FakeBigQueryClient()
    connector._project = "proj"
    connector._dataset = "dataset"
    connector._write_mode = "cdc"
    connector._batch_size = 100
    config = PipelineConfig(
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
            operations={"insert": ["c"], "update": ["u"], "delete": ["d"]},
        ),
    )

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
                    "op": "d",
                },
            ]
        ),
        "hash1",
        config.cdc,
    )

    assert connector._bq.created_tables
    assert connector._bq.load_jobs
    script = "\n".join(connector._bq.queries)
    assert "BEGIN TRANSACTION" in script
    assert "MERGE `proj.dataset.customers` AS dest" in script
    assert "ON dest.`customer_id` = staging.`customer_id`" in script
    assert "WHEN MATCHED AND staging.`_filedge_cdc_operation` = 'delete' THEN DELETE" in script
    assert "WHEN MATCHED THEN UPDATE SET" in script
    assert "WHEN NOT MATCHED AND staging.`_filedge_cdc_operation` <> 'delete' THEN INSERT" in script
    assert "INSERT INTO `proj.dataset._filedge_applied_files`" in script
    assert "COMMIT TRANSACTION" in script


def _install_fake_bigquery_modules(monkeypatch):
    google_module = types.ModuleType("google")
    cloud_module = types.ModuleType("google.cloud")
    api_core_module = types.ModuleType("google.api_core")
    exceptions_module = types.ModuleType("google.api_core.exceptions")
    exceptions_module.NotFound = type("NotFound", (Exception,), {})
    exceptions_module.Conflict = type("Conflict", (Exception,), {})

    class FakeSchemaField:
        def __init__(self, name, field_type, mode="NULLABLE"):
            self.name = name
            self.field_type = field_type
            self.mode = mode

    class FakeTableReference:
        def __init__(self, dataset_ref, table):
            self.dataset_ref = dataset_ref
            self.table_id = table

        def __str__(self):
            return f"{self.dataset_ref.project}.{self.dataset_ref.dataset_id}.{self.table_id}"

    class FakeDatasetReference:
        def __init__(self, project, dataset):
            self.project = project
            self.dataset_id = dataset

    class FakeTableObj:
        def __init__(self, ref, schema=None):
            self.ref = ref
            self.schema = schema or []

    class FakeLoadJobConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    bigquery_namespace = types.SimpleNamespace(
        Client=lambda project=None: FakeBigQueryClient(),
        DatasetReference=FakeDatasetReference,
        LoadJobConfig=FakeLoadJobConfig,
        SchemaField=FakeSchemaField,
        SourceFormat=types.SimpleNamespace(NEWLINE_DELIMITED_JSON="NDJSON"),
        Table=FakeTableObj,
        TableReference=FakeTableReference,
        WriteDisposition=types.SimpleNamespace(
            WRITE_TRUNCATE="WRITE_TRUNCATE",
            WRITE_APPEND="WRITE_APPEND",
        ),
    )
    cloud_module.bigquery = bigquery_namespace

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery_namespace)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core_module)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions_module)
