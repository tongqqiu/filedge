from dataclasses import dataclass, field
from typing import List

import yaml


@dataclass
class ColumnMapping:
    source: str
    dest: str
    type: str  # string, integer, float, date, timestamp, boolean
    required: bool = True


@dataclass
class PipelineConfig:
    format: str
    dest_table: str
    columns: List[ColumnMapping]
    retry_cap: int = 3
    stale_timeout_minutes: int = 30
    batch_size: int = 1000


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
    return PipelineConfig(
        format=data["format"],
        dest_table=data["dest_table"],
        columns=columns,
        retry_cap=data.get("retry_cap", 3),
        stale_timeout_minutes=data.get("stale_timeout_minutes", 30),
        batch_size=data.get("batch_size", 1000),
    )
