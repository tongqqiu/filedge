import datetime
import json
import os
import tempfile
from typing import Iterator, Optional

from filedge.cdc import plan_cdc_changes
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import PROVENANCE_COLUMN_NAMES, expected_columns, schema_mismatches

_TYPE_TO_BQ = {
    "string": "STRING",
    "integer": "INT64",
    "float": "FLOAT64",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOL",
}


def _safe_job_id_part(value: str, max_length: int) -> str:
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in value)
    return safe[:max_length] or "unknown"


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
        return [
            SchemaField(
                col.name,
                col.type,
                mode="REQUIRED" if col.name in PROVENANCE_COLUMN_NAMES else "NULLABLE",
            )
            for col in expected_columns(config, _TYPE_TO_BQ, "INT64", "TIMESTAMP")
        ]

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
            type_aliases={
                "INTEGER": "INT64",
                "FLOAT": "FLOAT64",
                "BOOLEAN": "BOOL",
            },
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

        safe_table = _safe_job_id_part(table, 40)
        safe_hash = _safe_job_id_part(file_hash, 40)
        job_id = f"etl_load_{safe_table}_{safe_hash}"

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

    def write_cdc_rows(
        self,
        table: str,
        rows: Iterator[dict],
        file_hash: str,
        cdc: CdcConfig,
    ) -> None:
        from google.cloud import bigquery

        changes = plan_cdc_changes(rows, cdc)
        self._ensure_applied_files_table()
        staging_table = f"_filedge_staging_{_safe_job_id_part(file_hash, 20)}_{int(datetime.datetime.now(datetime.UTC).timestamp())}"
        staging_ref = self._table_ref(staging_table)
        records = []
        for change in changes:
            record = {
                key: value
                for key, value in change.row.items()
                if key != cdc.operation_column
            }
            record["_filedge_cdc_operation"] = change.operation
            records.append(record)

        if records:
            schema = [
                bigquery.SchemaField(name, "STRING")
                for name in records[0].keys()
            ]
            self._bq.create_table(bigquery.Table(staging_ref, schema=schema))
            tmp_path = self._write_cdc_staging_file(records)
            try:
                with open(tmp_path, "rb") as f:
                    job = self._bq.load_table_from_file(
                        f,
                        staging_ref,
                        job_config=bigquery.LoadJobConfig(
                            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                        ),
                        job_id=f"filedge_cdc_stage_{_safe_job_id_part(table, 30)}_{_safe_job_id_part(file_hash, 30)}",
                    )
                    job.result()
            finally:
                os.unlink(tmp_path)

        script = self._cdc_apply_script(table, staging_table, records, cdc, file_hash)
        job = self._bq.query(script)
        job.result()

    def _write_cdc_staging_file(self, rows: list[dict]) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as tmp:
            for row in rows:
                tmp.write(json.dumps(row) + "\n")
            return tmp.name

    def _cdc_apply_script(
        self,
        table: str,
        staging_table: str,
        records: list[dict],
        cdc: CdcConfig,
        file_hash: str,
    ) -> str:
        target = self._table_sql(table)
        staging = self._table_sql(staging_table)
        marker = self._table_sql("_filedge_applied_files")
        escaped_table = self._sql_string(table)
        escaped_hash = self._sql_string(file_hash)

        statements = [
            "BEGIN TRANSACTION;",
            f"IF NOT EXISTS (SELECT 1 FROM {marker} WHERE destination_table = '{escaped_table}' AND content_hash = '{escaped_hash}') THEN",
        ]

        if records:
            data_columns = [
                col for col in records[0].keys() if col != "_filedge_cdc_operation"
            ]
            key_match = " AND ".join(
                f"dest.{self._quote(key)} = staging.{self._quote(key)}"
                for key in cdc.keys
            )
            update_assignments = ", ".join(
                [f"dest.{self._quote(col)} = staging.{self._quote(col)}" for col in data_columns]
                + [
                    f"dest.{self._quote('_source_file_hash')} = '{escaped_hash}'",
                    f"dest.{self._quote('_ingested_at')} = CURRENT_TIMESTAMP()",
                ]
            )
            insert_columns = data_columns + ["_source_file_hash", "_ingested_at"]
            insert_cols = ", ".join(self._quote(col) for col in insert_columns)
            insert_values = ", ".join(
                [f"staging.{self._quote(col)}" for col in data_columns]
                + [f"'{escaped_hash}'", "CURRENT_TIMESTAMP()"]
            )
            statements.append(
                f"MERGE {target} AS dest "
                f"USING {staging} AS staging "
                f"ON {key_match} "
                "WHEN MATCHED AND staging.`_filedge_cdc_operation` = 'delete' THEN DELETE "
                f"WHEN MATCHED THEN UPDATE SET {update_assignments} "
                "WHEN NOT MATCHED AND staging.`_filedge_cdc_operation` <> 'delete' "
                f"THEN INSERT ({insert_cols}) VALUES ({insert_values});"
            )

        statements.extend(
            [
                f"INSERT INTO {marker} (destination_table, content_hash, applied_at) "
                f"VALUES ('{escaped_table}', '{escaped_hash}', CURRENT_TIMESTAMP());",
                "END IF;",
                "COMMIT TRANSACTION;",
            ]
        )
        if records:
            statements.append(f"DROP TABLE {staging};")
        return "\n".join(statements)

    def _ensure_applied_files_table(self) -> None:
        from google.cloud import bigquery
        from google.api_core.exceptions import NotFound

        marker_ref = self._table_ref("_filedge_applied_files")
        try:
            self._bq.get_table(marker_ref)
        except NotFound:
            self._bq.create_table(
                bigquery.Table(
                    marker_ref,
                    schema=[
                        bigquery.SchemaField("destination_table", "STRING", mode="REQUIRED"),
                        bigquery.SchemaField("content_hash", "STRING", mode="REQUIRED"),
                        bigquery.SchemaField("applied_at", "TIMESTAMP", mode="REQUIRED"),
                    ],
                )
            )

    def _table_sql(self, table: str) -> str:
        return f"`{self._project}.{self._dataset}.{table}`"

    def _quote(self, identifier: str) -> str:
        return "`" + identifier.replace("`", "``") + "`"

    def _sql_string(self, value: str) -> str:
        return value.replace("'", "''")

    def healthcheck(self) -> None:
        job = self._bq.query("SELECT 1")
        list(job.result())

    def close(self) -> None:
        self._bq.close()
