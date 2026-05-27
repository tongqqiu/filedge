from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

from filedge.column_types import validate_column_type


@dataclass
class EncryptConfig:
    algorithm: str
    key: str


@dataclass
class HashConfig:
    algorithm: str
    key: str


@dataclass
class ColumnMapping:
    source: str
    dest: str
    type: str  # string, integer, float, date, timestamp, boolean
    required: bool = True
    encrypt: Optional[EncryptConfig] = None
    hash: Optional[HashConfig] = None


@dataclass
class ConnectorConfig:
    type: str
    options: Dict[str, str] = field(default_factory=dict)


@dataclass
class CdcConfig:
    keys: List[str]
    operation_column: str
    sequence_by: str
    operations: Dict[str, List[str]]


@dataclass
class PipelineConfig:
    format: str
    dest_table: str
    columns: List[ColumnMapping]
    retry_cap: int = 3
    stale_timeout_minutes: int = 30
    batch_size: int = 1000
    write_mode: str = "append"
    connector: Optional[ConnectorConfig] = None
    encoding: str = "utf-8"
    cdc: Optional[CdcConfig] = None
    file_pattern: Optional[str] = None
    source_manifest: str = "optional"


def load_config(path: str) -> PipelineConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    columns = [
        ColumnMapping(
            source=c["source"],
            dest=c["dest"],
            type=c["type"],
            required=c.get("required", True),
            encrypt=_parse_encrypt_config(c.get("encrypt"), c),
            hash=_parse_hash_config(c.get("hash")),
        )
        for c in data["columns"]
    ]
    for column in columns:
        validate_column_type(column.type)

    connector = None
    if "connector" in data:
        raw = dict(data["connector"])
        connector_type = raw.pop("type")
        connector = ConnectorConfig(type=connector_type, options=raw)
    cdc = None
    if "cdc" in data:
        raw_cdc = data["cdc"]
        cdc = CdcConfig(
            keys=list(raw_cdc["keys"]),
            operation_column=raw_cdc["operation_column"],
            sequence_by=raw_cdc["sequence_by"],
            operations={
                op: list(values) for op, values in raw_cdc["operations"].items()
            },
        )
    write_mode = data.get("write_mode", "append")
    if write_mode == "cdc" and cdc is None:
        raise ValueError("write_mode: cdc requires a cdc: block")
    if cdc is not None:
        declared_sources = {column.source for column in columns}
        for key in cdc.keys:
            if key not in declared_sources:
                raise ValueError(f"CDC key column {key!r} must be declared in columns")
        if cdc.sequence_by not in declared_sources:
            raise ValueError(
                f"CDC sequence column {cdc.sequence_by!r} must be declared in columns"
            )

    return PipelineConfig(
        format=data["format"],
        dest_table=data["dest_table"],
        columns=columns,
        retry_cap=data.get("retry_cap", 3),
        stale_timeout_minutes=data.get("stale_timeout_minutes", 30),
        batch_size=data.get("batch_size", 1000),
        write_mode=write_mode,
        connector=connector,
        encoding=data.get("encoding", "utf-8"),
        cdc=cdc,
        file_pattern=data.get("file_pattern"),
        source_manifest=_validate_source_manifest(data.get("source_manifest", "optional")),
    )


def _validate_source_manifest(value: str) -> str:
    if value not in ("disabled", "optional", "required"):
        raise ValueError(
            f"source_manifest must be one of disabled/optional/required, got {value!r}"
        )
    return value


def _parse_encrypt_config(
    raw: Optional[Dict[str, str]], column: Dict[str, object]
) -> Optional[EncryptConfig]:
    if raw is None:
        return None
    if column["type"] != "string":
        raise ValueError("encrypt columns must declare type: string")
    algorithm = _required_crypto_field(raw, "algorithm", "encrypt")
    if algorithm != "aes-256-gcm":
        raise ValueError(f"Unsupported encrypt algorithm: {algorithm!r}")
    key = _required_crypto_field(raw, "key", "encrypt")
    _validate_key_reference(key)
    return EncryptConfig(algorithm=algorithm, key=key)


def _parse_hash_config(raw: Optional[Dict[str, str]]) -> Optional[HashConfig]:
    if raw is None:
        return None
    algorithm = _required_crypto_field(raw, "algorithm", "hash")
    if algorithm != "hmac-sha256":
        raise ValueError(f"Unsupported hash algorithm: {algorithm!r}")
    key = _required_crypto_field(raw, "key", "hash")
    _validate_key_reference(key)
    return HashConfig(algorithm=algorithm, key=key)


def _required_crypto_field(raw: Dict[str, str], field: str, block: str) -> str:
    value = raw.get(field)
    if not value:
        raise ValueError(f"{block}: requires {field}:")
    return value


def _validate_key_reference(value: str) -> None:
    if value.startswith("env:") and value.removeprefix("env:"):
        return
    if value.startswith("secrets:/") and value.removeprefix("secrets:"):
        return
    raise ValueError(
        "Field Encryption key reference must use env:NAME or secrets:/absolute/path"
    )
