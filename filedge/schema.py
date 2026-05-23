from dataclasses import dataclass
from typing import Dict, Iterable, List

from filedge.config import PipelineConfig


@dataclass(frozen=True)
class ExpectedColumn:
    name: str
    type: str


def expected_columns(
    config: PipelineConfig,
    type_map: Dict[str, str],
    id_type: str,
    ingested_at_type: str,
) -> List[ExpectedColumn]:
    columns = [ExpectedColumn("_id", id_type)]
    columns.extend(
        ExpectedColumn(col.dest, type_map.get(col.type, type_map["string"]))
        for col in config.columns
    )
    columns.append(ExpectedColumn("_source_file_hash", type_map["string"]))
    columns.append(ExpectedColumn("_ingested_at", ingested_at_type))
    return columns


def schema_mismatches(
    existing: Dict[str, str],
    expected: Iterable[ExpectedColumn],
    *,
    type_aliases: Dict[str, str] | None = None,
) -> List[str]:
    aliases = type_aliases or {}
    expected_by_name = {col.name: _normalize_type(col.type, aliases) for col in expected}
    existing_by_name = {
        name: _normalize_type(col_type, aliases)
        for name, col_type in existing.items()
    }

    mismatches: List[str] = []
    for name in sorted(expected_by_name):
        if name not in existing_by_name:
            mismatches.append(f"  Column '{name}' declared in pipeline.yaml but missing from table")
            continue
        if existing_by_name[name] != expected_by_name[name]:
            mismatches.append(
                f"  Column '{name}' has type {existing_by_name[name]!r}"
                f" but pipeline.yaml expects {expected_by_name[name]!r}"
            )

    for name in sorted(existing_by_name):
        if name not in expected_by_name:
            mismatches.append(f"  Column '{name}' exists in table but is not declared in pipeline.yaml")

    return mismatches


def _normalize_type(col_type: str, aliases: Dict[str, str]) -> str:
    normalized = " ".join(str(col_type).strip().upper().split())
    return aliases.get(normalized, normalized)
