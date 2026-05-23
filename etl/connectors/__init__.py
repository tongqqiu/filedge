from abc import ABC, abstractmethod
from typing import Iterator

from etl.config import PipelineConfig


class SchemaError(Exception):
    """Raised when the destination table schema does not match the Pipeline Config."""


class Connector(ABC):
    @abstractmethod
    def ensure_table(self, config: PipelineConfig) -> None:
        """Create or validate the destination table against the Pipeline Config.

        Raises SchemaError if the table already exists with a schema that does
        not match the declared columns.
        """

    @abstractmethod
    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None:
        """Write rows to table.

        Must be idempotent per file_hash: calling write_rows twice with the
        same file_hash must produce the same destination state as calling it
        once. Implementations typically achieve this via a DELETE WHERE
        _source_file_hash = file_hash followed by INSERT (append mode), or by
        encoding file_hash in a destination-native job ID (BigQuery).

        Every written row must include _source_file_hash and _ingested_at
        provenance columns.

        Raises SchemaError if the destination table schema is incompatible.
        """

    def close(self) -> None:
        pass


_REGISTRY = {
    "sqlite": "etl.connectors.sqlite.SQLiteConnector",
    "postgres": "etl.connectors.postgres.PostgresConnector",
    "bigquery": "etl.connectors.bigquery.BigQueryConnector",
    "databricks": "etl.connectors.databricks.DatabricksConnector",
    "duckdb": "etl.connectors.duckdb.DuckDBConnector",
}

_INSTALL_HINTS = {
    "postgres": "pip install etl-big-idea[postgres]",
    "bigquery": "pip install etl-big-idea[bigquery]",
    "databricks": "pip install etl-big-idea[databricks]",
    "duckdb": "pip install etl-big-idea[duckdb]",
}


def _load_class(dotted_path: str, connector_type: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    try:
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except ImportError as e:
        hint = _INSTALL_HINTS.get(connector_type, "")
        suffix = f" — run: {hint}" if hint else ""
        raise ImportError(
            f"Connector '{connector_type}' requires an optional dependency{suffix}"
        ) from e


def get_connector(config: PipelineConfig) -> "Connector":
    if config.connector is None:
        raise ValueError(
            "pipeline.yaml is missing a connector: block. Add one, e.g.:\n"
            "  connector:\n"
            "    type: sqlite\n"
            "    url: sqlite:///path/to/dest.db"
        )

    connector_type = config.connector.type
    options = config.connector.options

    if connector_type not in _REGISTRY:
        raise ValueError(
            f"Unknown connector type '{connector_type}'. "
            f"Known types: {', '.join(sorted(_REGISTRY))}"
        )

    cls = _load_class(_REGISTRY[connector_type], connector_type)
    return cls(
        write_mode=config.write_mode,
        batch_size=config.batch_size,
        **options,
    )
