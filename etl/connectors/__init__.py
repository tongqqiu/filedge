from abc import ABC, abstractmethod
from typing import Iterator

from etl.config import PipelineConfig


class Connector(ABC):
    @abstractmethod
    def ensure_table(self, config: PipelineConfig) -> None: ...

    @abstractmethod
    def write_rows(self, table: str, rows: Iterator[dict], file_hash: str) -> None: ...

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


def get_connector(config: PipelineConfig, audit_db_url: str = "") -> "Connector":
    if config.connector is not None:
        connector_type = config.connector.type
        options = config.connector.options
    else:
        # Infer from audit DB URL for backward compatibility
        if audit_db_url.startswith("sqlite:///"):
            connector_type = "sqlite"
            options = {"url": audit_db_url}
        elif audit_db_url.startswith("postgresql://") or audit_db_url.startswith("postgres://"):
            connector_type = "postgres"
            options = {"url": audit_db_url}
        else:
            raise ValueError(
                "No connector: block in pipeline.yaml and cannot infer connector from audit DB URL"
            )

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
