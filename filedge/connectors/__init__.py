from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

from filedge.config import CdcConfig, PipelineConfig


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

    def write_cdc_rows(
        self,
        table: str,
        rows: Iterator[dict],
        file_hash: str,
        cdc: CdcConfig,
    ) -> None:
        raise NotImplementedError(
            f"Connector {type(self).__name__} does not support write_mode: cdc"
        )

    def healthcheck(self) -> None:
        """Perform a read-only round-trip against the destination."""
        raise NotImplementedError(
            f"Connector {type(self).__name__} does not support healthcheck"
        )

    def close(self) -> None:
        pass


_REGISTRY = {
    "sqlite": "filedge.connectors.sqlite.SQLiteConnector",
    "postgres": "filedge.connectors.postgres.PostgresConnector",
    "bigquery": "filedge.connectors.bigquery.BigQueryConnector",
    "databricks": "filedge.connectors.databricks.DatabricksConnector",
    "duckdb": "filedge.connectors.duckdb.DuckDBConnector",
}

_INSTALL_HINTS = {
    "postgres": "pip install filedge[postgres]",
    "bigquery": "pip install filedge[bigquery]",
    "databricks": "pip install filedge[databricks]",
    "duckdb": "pip install filedge[duckdb]",
}


@dataclass(frozen=True)
class ConnectorSetting:
    """One non-secret connector setting safe to write to pipeline.yaml."""

    name: str
    required: bool = True
    default: str = ""


@dataclass(frozen=True)
class CredentialPlaceholder:
    """One runtime environment variable a Connector expects for credentials."""

    env_var: str
    purpose: str


@dataclass(frozen=True)
class ConnectorDescriptor:
    """Authoring-safe metadata for a Connector Registry entry."""

    type: str
    settings: tuple[ConnectorSetting, ...] = ()
    credential_placeholders: tuple[CredentialPlaceholder, ...] = ()


_DESCRIPTORS = {
    "sqlite": ConnectorDescriptor(
        type="sqlite",
        settings=(ConnectorSetting("url", default="sqlite:///REPLACE_ME.db"),),
    ),
    "postgres": ConnectorDescriptor(
        type="postgres",
        credential_placeholders=(
            CredentialPlaceholder("DATABASE_URL", "PostgreSQL connection URL"),
        ),
    ),
    "bigquery": ConnectorDescriptor(
        type="bigquery",
        settings=(ConnectorSetting("project"), ConnectorSetting("dataset")),
        credential_placeholders=(
            CredentialPlaceholder(
                "GOOGLE_APPLICATION_CREDENTIALS",
                "BigQuery Application Default Credentials",
            ),
        ),
    ),
    "databricks": ConnectorDescriptor(
        type="databricks",
        settings=(
            ConnectorSetting("server_hostname"),
            ConnectorSetting("http_path"),
            ConnectorSetting("catalog"),
            ConnectorSetting("schema"),
        ),
        credential_placeholders=(
            CredentialPlaceholder("DATABRICKS_TOKEN", "Databricks access token"),
        ),
    ),
    "duckdb": ConnectorDescriptor(
        type="duckdb",
        settings=(ConnectorSetting("path", default="./analytics.duckdb"),),
    ),
}


def available_connector_types() -> list[str]:
    """Return Connector Registry types in stable UI order without importing SDKs."""
    return sorted(_REGISTRY)


def connector_descriptor(connector_type: str) -> ConnectorDescriptor:
    """Return authoring-safe metadata for one Connector Registry entry."""
    if connector_type not in _REGISTRY:
        raise ValueError(
            f"Unknown connector type '{connector_type}'. "
            f"Known types: {', '.join(available_connector_types())}"
        )
    return _DESCRIPTORS[connector_type]


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
