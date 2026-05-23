import datetime
import json
import os
import shutil
import tempfile
import uuid
from typing import Dict, Iterator, List, Optional
from urllib.parse import urlparse

from filedge.config import PipelineConfig
from filedge.connectors import Connector, SchemaError
from filedge.schema import expected_columns, schema_mismatches

_TYPE_TO_SQL = {
    "string": "STRING",
    "integer": "BIGINT",
    "float": "DOUBLE",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOLEAN",
}

_CLOUD_SCHEMES = {"s3", "s3a", "gs", "gcs", "abfs", "abfss", "az", "adl"}


class DatabricksConnector(Connector):
    def __init__(
        self,
        server_hostname: Optional[str] = None,
        http_path: Optional[str] = None,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
        staging_location: Optional[str] = None,
        write_mode: str = "append",
        batch_size: int = 1000,
        **_,
    ):
        try:
            from databricks import sql
        except ImportError as e:
            raise ImportError(
                "Databricks connector requires an optional dependency"
                " — run: pip install filedge[databricks]"
            ) from e

        missing = [
            name
            for name, value in {
                "server_hostname": server_hostname,
                "http_path": http_path,
                "catalog": catalog,
                "schema": schema,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                "DatabricksConnector requires these connector fields: "
                + ", ".join(missing)
            )

        token = os.environ.get("DATABRICKS_TOKEN")
        if not token:
            raise ValueError("DatabricksConnector requires DATABRICKS_TOKEN to be set")

        self._conn = sql.connect(
            server_hostname=server_hostname,
            http_path=http_path,
            access_token=token,
            catalog=catalog,
            schema=schema,
        )
        self._catalog = str(catalog)
        self._schema = str(schema)
        self._staging_location = staging_location or os.environ.get(
            "DATABRICKS_STAGING_LOCATION"
        )
        self._write_mode = write_mode
        self._batch_size = batch_size

    def ensure_table(self, config: PipelineConfig) -> None:
        existing = self._get_existing_columns(config.dest_table)
        if existing is None:
            self._create_table(config)
            return

        mismatches = schema_mismatches(
            existing,
            expected_columns(config, _TYPE_TO_SQL, "BIGINT", "TIMESTAMP"),
            type_aliases={
                "LONG": "BIGINT",
                "VARCHAR": "STRING",
                "CHAR": "STRING",
            },
        )
        if mismatches:
            raise SchemaError(
                f"Schema mismatch for table '{config.dest_table}':\n"
                + "\n".join(mismatches)
            )

    def _get_existing_columns(self, table: str) -> Optional[Dict[str, str]]:
        sql = (
            "SELECT column_name, data_type"
            f" FROM {self._quote(self._catalog)}.information_schema.columns"
            f" WHERE table_schema = '{self._string_literal(self._schema)}'"
            f" AND table_name = '{self._string_literal(table)}'"
            " ORDER BY ordinal_position"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        if not rows:
            return None
        return {row[0]: str(row[1]).upper() for row in rows}

    def _create_table(self, config: PipelineConfig) -> None:
        col_defs = ["_id BIGINT GENERATED ALWAYS AS IDENTITY"]
        for col in config.columns:
            col_defs.append(f"{self._quote(col.dest)} {_TYPE_TO_SQL.get(col.type, 'STRING')}")
        col_defs.append("_source_file_hash STRING NOT NULL")
        col_defs.append("_ingested_at TIMESTAMP NOT NULL")
        ddl = f"CREATE TABLE {self._table_ref(config.dest_table)} ({', '.join(col_defs)})"
        with self._conn.cursor() as cur:
            cur.execute(ddl)

    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        first = next(rows, None)
        if first is None:
            if self._write_mode == "truncate":
                with self._conn.cursor() as cur:
                    cur.execute(f"TRUNCATE TABLE {self._table_ref(table)}")
            return

        if not self._staging_location:
            raise ValueError(
                "DatabricksConnector requires 'staging_location' in the connector block "
                "or DATABRICKS_STAGING_LOCATION for COPY INTO staging"
            )

        ingested_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
        row_columns = list(first.keys())
        load_columns = row_columns + ["_source_file_hash", "_ingested_at"]
        staging_table = f"_filedge_staging_{uuid.uuid4().hex}"
        staging_uri = self._staging_uri(file_hash)
        local_path = self._write_local_staging_file(
            first, rows, file_hash, ingested_at, staging_uri
        )

        try:
            self._upload_staging_file(local_path, staging_uri)
            self._load_and_commit(table, staging_table, staging_uri, load_columns)
        finally:
            os.unlink(local_path)
            self._remove_staging_file(staging_uri)

    def _write_local_staging_file(
        self,
        first: dict,
        rows: Iterator[dict],
        file_hash: str,
        ingested_at: str,
        staging_uri: str,
    ) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            for row in _chain_first(first, rows):
                record = dict(row)
                record["_source_file_hash"] = file_hash
                record["_ingested_at"] = ingested_at
                tmp.write(json.dumps(record) + "\n")
            return tmp.name

    def _load_and_commit(
        self,
        table: str,
        staging_table: str,
        staging_uri: str,
        load_columns: List[str],
    ) -> None:
        dest = self._table_ref(table)
        staging = self._table_ref(staging_table)
        column_defs = ", ".join(
            f"{self._quote(col)} {self._staging_column_type(col)}"
            for col in load_columns
        )
        cols = ", ".join(self._quote(col) for col in load_columns)
        merge_values = ", ".join(f"staging.{self._quote(col)}" for col in load_columns)

        try:
            with self._conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {staging} ({column_defs})")
                cur.execute(
                    f"COPY INTO {staging} ({cols})"
                    f" FROM '{self._string_literal(staging_uri)}'"
                    " FILEFORMAT = JSON"
                )
                if self._write_mode == "truncate":
                    cur.execute(f"TRUNCATE TABLE {dest}")
                    cur.execute(
                        f"INSERT INTO {dest} ({cols}) SELECT {cols} FROM {staging}"
                    )
                else:
                    cur.execute(
                        f"MERGE INTO {dest} AS dest"
                        f" USING {staging} AS staging"
                        " ON dest._source_file_hash = staging._source_file_hash"
                        f" WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({merge_values})"
                    )
        finally:
            with self._conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {staging}")

    def _staging_column_type(self, column: str) -> str:
        if column == "_source_file_hash":
            return "STRING"
        if column == "_ingested_at":
            return "TIMESTAMP"
        return "STRING"

    def _staging_uri(self, file_hash: str) -> str:
        safe_hash = "".join(c if c.isalnum() else "_" for c in file_hash[:40])
        filename = f"filedge_{safe_hash}_{uuid.uuid4().hex}.json"
        return self._staging_location.rstrip("/") + "/" + filename

    def _upload_staging_file(self, local_path: str, staging_uri: str) -> None:
        parsed = urlparse(staging_uri)
        if parsed.scheme in _CLOUD_SCHEMES:
            try:
                import fsspec
            except ImportError as e:
                raise ImportError(
                    "Databricks cloud staging requires fsspec"
                    " — run: pip install filedge[databricks] and the cloud extra"
                ) from e
            with open(local_path, "rb") as source, fsspec.open(staging_uri, "wb") as dest:
                shutil.copyfileobj(source, dest)
            return

        target = parsed.path if parsed.scheme == "file" else staging_uri
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(local_path, target)

    def _remove_staging_file(self, staging_uri: str) -> None:
        parsed = urlparse(staging_uri)
        try:
            if parsed.scheme in _CLOUD_SCHEMES:
                import fsspec

                fs, _, paths = fsspec.get_fs_token_paths(staging_uri)
                fs.rm(paths[0])
                return
            target = parsed.path if parsed.scheme == "file" else staging_uri
            os.unlink(target)
        except Exception:
            pass

    def _table_ref(self, table: str) -> str:
        return ".".join(
            [self._quote(self._catalog), self._quote(self._schema), self._quote(table)]
        )

    def _quote(self, identifier: str) -> str:
        return "`" + identifier.replace("`", "``") + "`"

    def _string_literal(self, value: str) -> str:
        return value.replace("'", "''")

    def close(self) -> None:
        self._conn.close()


def _chain_first(first: dict, rows: Iterator[dict]) -> Iterator[dict]:
    yield first
    yield from rows
