import datetime
import json
import os
import tempfile
from typing import Iterator, Optional

from filedge.config import PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import expected_columns, schema_mismatches

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
                " — run: pip install filedge[bigquery]"
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

        existing_columns = {f.name: f.field_type for f in existing.schema}
        mismatches = schema_mismatches(
            existing_columns,
            expected_columns(config, _TYPE_TO_BQ, "INT64", "TIMESTAMP"),
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n"
                + "\n".join(mismatches)
            )

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        from google.cloud import bigquery
        from google.api_core.exceptions import Conflict

        ingested_at = datetime.datetime.now(datetime.UTC).isoformat()
        write_disposition = (
            bigquery.WriteDisposition.WRITE_TRUNCATE
            if self._write_mode == "truncate"
            else bigquery.WriteDisposition.WRITE_APPEND
        )

        # NOTE: job-ID deduplication is only guaranteed for 7 days. After that,
        # retrying the same file_hash will produce a new job and append duplicate
        # rows (append mode). For long-lived pipelines, prefer truncate mode or
        # implement a pre-load DELETE via BigQuery DML.

        safe_hash = file_hash[:40].replace("/", "_").replace("+", "_")
        job_id = f"etl_load_{safe_hash}"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as tmp:
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
            )

            with open(tmp_path, "rb") as f:
                try:
                    job = self._bq.load_table_from_file(
                        f, table_ref, job_config=job_config, job_id=job_id
                    )
                    job.result()
                except Conflict:
                    # A job with this ID already exists from a previous attempt.
                    existing = self._bq.get_job(job_id)
                    if existing.state == "DONE" and not existing.errors:
                        return  # previous run succeeded — idempotent, nothing to do
                    # Previous job failed; BigQuery won't accept the same job ID again,
                    # so retry under a unique ID.
                    fallback_id = f"{job_id}_{int(datetime.datetime.now(datetime.UTC).timestamp())}"
                    f.seek(0)
                    job = self._bq.load_table_from_file(
                        f, table_ref, job_config=job_config, job_id=fallback_id
                    )
                    job.result()
        finally:
            if tmp_path:
                os.unlink(tmp_path)

    def close(self) -> None:
        self._bq.close()
