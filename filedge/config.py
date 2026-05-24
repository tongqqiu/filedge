from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

from filedge.column_types import validate_column_type


@dataclass
class ColumnMapping:
    source: str
    dest: str
    type: str  # string, integer, float, date, timestamp, boolean
    required: bool = True


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


def load_config(path: str) -> PipelineConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    columns = [
        ColumnMapping(
            source=c["source"],
            dest=c["dest"],
            type=c["type"],
            required=c.get("required", True),
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
    )
