from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


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
    connector = None
    if "connector" in data:
        raw = dict(data["connector"])
        connector_type = raw.pop("type")
        connector = ConnectorConfig(type=connector_type, options=raw)

    return PipelineConfig(
        format=data["format"],
        dest_table=data["dest_table"],
        columns=columns,
        retry_cap=data.get("retry_cap", 3),
        stale_timeout_minutes=data.get("stale_timeout_minutes", 30),
        batch_size=data.get("batch_size", 1000),
        write_mode=data.get("write_mode", "append"),
        connector=connector,
        encoding=data.get("encoding", "utf-8"),
    )
