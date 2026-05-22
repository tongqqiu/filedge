import datetime
import json
import tempfile
from typing import Iterator, Optional

from etl.config import PipelineConfig
from etl.connectors import Connector
from etl.db import SchemaError

_TYPE_TO_BQ = {
    "string": "STRING",
    "integer": "INT64",
    "float": "FLOAT64",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOL",
}


class BigQueryConnector(Connector):
    def __init__(
        self,
        write_mode: str = "append",
        batch_size: int = 1000,
        project: Optional[str] = None,
        dataset: Optional[str] = None,
        **_,
    ):
        try:
            from google.cloud import bigquery
        except ImportError as e:
            raise ImportError(
                "BigQuery connector requires an optional dependency"
                " — run: pip install etl-big-idea[bigquery]"
            ) from e

        if not project or not dataset:
            raise ValueError(
                "BigQueryConnector requires 'project' and 'dataset' in the connector: block"
            )

        self._bq = bigquery.Client(project=project)
        self._project = project
        self._dataset = dataset
        self._write_mode = write_mode
        self._batch_size = batch_size

    def _table_ref(self, table: str):
        from google.cloud import bigquery
        return bigquery.TableReference(
            bigquery.DatasetReference(self._project, self._dataset), table
        )

    def _bq_schema(self, config: PipelineConfig):
        from google.cloud.bigquery import SchemaField
        fields = [
            SchemaField(col.dest, _TYPE_TO_BQ.get(col.type, "STRING"))
            for col in config.columns
        ]
        fields.append(SchemaField("_source_file_hash", "STRING", mode="REQUIRED"))
        fields.append(SchemaField("_ingested_at", "TIMESTAMP", mode="REQUIRED"))
        return fields

    def ensure_table(self, config: PipelineConfig) -> None:
        from google.cloud import bigquery
        from google.api_core.exceptions import NotFound

        table_ref = self._table_ref(config.dest_table)
        try:
            existing = self._bq.get_table(table_ref)
        except NotFound:
            table = bigquery.Table(table_ref, schema=self._bq_schema(config))
            self._bq.create_table(table)
            return

        existing_names = {f.name for f in existing.schema}
        required = {col.dest for col in config.columns} | {"_source_file_hash", "_ingested_at"}
        missing = sorted(required - existing_names)
        if missing:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n"
                + "\n".join(f"  Column '{n}' declared in pipeline.yaml but missing from table"
                            for n in missing)
            )

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        from google.cloud import bigquery

        ingested_at = datetime.datetime.now(datetime.UTC).isoformat()
        write_disposition = (
            bigquery.WriteDisposition.WRITE_TRUNCATE
            if self._write_mode == "truncate"
            else bigquery.WriteDisposition.WRITE_APPEND
        )

        # Stream rows to a newline-delimited JSON temp file, then bulk load
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ndjson", delete=False
        ) as tmp:
            tmp_path = tmp.name
            for row in rows:
                record = dict(row)
                record["_source_file_hash"] = file_hash
                record["_ingested_at"] = ingested_at
                tmp.write(json.dumps(record) + "\n")

        table_ref = self._table_ref(table)
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=write_disposition,
            # Use file_hash as job ID suffix for idempotency on append mode
        )

        with open(tmp_path, "rb") as f:
            # job_id prefix encodes file_hash so BigQuery deduplicates retries
            safe_hash = file_hash[:40].replace("/", "_").replace("+", "_")
            job_id = f"etl_load_{safe_hash}"
            try:
                job = self._bq.load_table_from_file(
                    f, table_ref, job_config=job_config, job_id=job_id
                )
                job.result()
            except Exception as e:
                # If job already exists (duplicate retry), treat as success
                if "Already Exists" in str(e):
                    return
                raise

    def close(self) -> None:
        self._bq.close()
