from dataclasses import dataclass
from typing import Dict, Iterable, List

from filedge.config import PipelineConfig


@dataclass(frozen=True)
class ExpectedColumn:
    name: str
    type: str


SOURCE_FILE_HASH_COLUMN = "_source_file_hash"
INGESTED_AT_COLUMN = "_ingested_at"
PROVENANCE_COLUMN_NAMES = frozenset({SOURCE_FILE_HASH_COLUMN, INGESTED_AT_COLUMN})


def configured_columns(
    config: PipelineConfig,
    type_map: Dict[str, str],
) -> List[ExpectedColumn]:
    return [
        ExpectedColumn(col.dest, type_map.get(col.type, type_map["string"]))
        for col in config.columns
    ]


def provenance_columns(
    type_map: Dict[str, str],
    ingested_at_type: str,
) -> List[ExpectedColumn]:
    return [
        ExpectedColumn(SOURCE_FILE_HASH_COLUMN, type_map["string"]),
        ExpectedColumn(INGESTED_AT_COLUMN, ingested_at_type),
    ]


def expected_columns(
    config: PipelineConfig,
    type_map: Dict[str, str],
    id_type: str,
    ingested_at_type: str,
) -> List[ExpectedColumn]:
    return [
        ExpectedColumn("_id", id_type),
        *configured_columns(config, type_map),
        *provenance_columns(type_map, ingested_at_type),
    ]


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
