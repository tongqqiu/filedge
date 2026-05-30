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
    # Fixed-width layout. Both are None for every other format.
    start: Optional[int] = None  # 1-indexed byte position
    width: Optional[int] = None


@dataclass
class ConnectorConfig:
    type: str
    options: Dict[str, str] = field(default_factory=dict)


@dataclass
class ExcelConfig:
    """Excel-specific options for `format: excel` (ADR-0012)."""

    sheet: object  # str | int; validated at load time


@dataclass
class CdcConfig:
    keys: List[str]
    operation_column: str
    sequence_by: str
    operations: Dict[str, List[str]]


@dataclass
class QuarantineConfig:
    """Opt-in Dead-Letter Quarantine policy for a Pipeline (ADR-0019).

    When enabled, rows that fail Transform/Field Encryption are written to an
    NDJSON quarantine sidecar in ``dir`` and the good rows still commit — unless
    the bad-row count exceeds the threshold, in which case the whole File fails
    (nothing committed), preserving the Strict Mode signal (ADR-0003). At least
    one of ``max_invalid_fraction`` / ``max_invalid_rows`` bounds the threshold;
    a File is over-threshold if it exceeds *either* configured limit.
    """

    dir: str
    max_invalid_fraction: Optional[float] = None
    max_invalid_rows: Optional[int] = None

    def is_over_threshold(self, invalid: int, total: int) -> bool:
        """True when this File's bad rows exceed a configured limit."""
        if self.max_invalid_rows is not None and invalid > self.max_invalid_rows:
            return True
        if self.max_invalid_fraction is not None and total > 0:
            if (invalid / total) > self.max_invalid_fraction:
                return True
        return False


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
    excel: Optional[ExcelConfig] = None
    quarantine: Optional[QuarantineConfig] = None


def load_config(path: str) -> PipelineConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return config_from_dict(data)


def config_from_dict(data: dict) -> PipelineConfig:
    """Build a `PipelineConfig` from an already-parsed config mapping.

    The dict-in entry point shared by `load_config` (which reads YAML from disk)
    and the Authoring Workflow, where a Pipeline Config Draft emits a config
    mapping that must round-trip through the same validation rules without a
    temporary file. All loading and validation lives here; `load_config` only
    owns reading the file.
    """
    columns = [
        ColumnMapping(
            source=c["source"],
            dest=c["dest"],
            type=c["type"],
            required=c.get("required", True),
            encrypt=_parse_encrypt_config(c.get("encrypt"), c),
            hash=_parse_hash_config(c.get("hash")),
            start=c.get("start"),
            width=c.get("width"),
        )
        for c in data["columns"]
    ]
    for column in columns:
        validate_column_type(column.type)

    if data["format"] == "fixed_width":
        _validate_fixed_width_columns(columns, data["columns"])

    excel = _parse_excel_config(data)

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
            if key and key not in declared_sources:
                raise ValueError(f"CDC key column {key!r} must be declared in columns")
        if cdc.sequence_by and cdc.sequence_by not in declared_sources:
            raise ValueError(
                f"CDC sequence column {cdc.sequence_by!r} must be declared in columns"
            )

    quarantine = _parse_quarantine_config(data.get("quarantine"))

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
        excel=excel,
        quarantine=quarantine,
    )


def _parse_quarantine_config(raw) -> Optional[QuarantineConfig]:
    """Parse the opt-in `quarantine:` block; absent or `enabled: false` → None.

    A disabled (or absent) block means Strict Mode (ADR-0003) — the default.
    When enabled, a `dir:` and at least one threshold limit are required, and
    each limit must be in range.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("quarantine: must be a mapping.")
    if not raw.get("enabled", False):
        return None

    directory = raw.get("dir")
    if not directory:
        raise ValueError("quarantine: enabled requires a 'dir' for the sidecar output.")

    fraction = raw.get("max_invalid_fraction")
    rows = raw.get("max_invalid_rows")
    if fraction is None and rows is None:
        raise ValueError(
            "quarantine: enabled requires a threshold "
            "(max_invalid_fraction and/or max_invalid_rows)."
        )
    if fraction is not None and not (0 <= float(fraction) <= 1):
        raise ValueError("quarantine: max_invalid_fraction must be between 0 and 1.")
    if rows is not None and int(rows) < 0:
        raise ValueError("quarantine: max_invalid_rows must be non-negative.")

    return QuarantineConfig(
        dir=directory,
        max_invalid_fraction=float(fraction) if fraction is not None else None,
        max_invalid_rows=int(rows) if rows is not None else None,
    )


def _validate_fixed_width_columns(
    columns: List[ColumnMapping], raw_columns: List[Dict[str, object]]
) -> None:
    from filedge.fixed_width import layout_from_columns, validate_layout

    for column, raw in zip(columns, raw_columns):
        if "start" not in raw or "width" not in raw:
            raise ValueError(
                f"fixed_width column {column.source!r} requires both start: and width:."
            )
    validate_layout(layout_from_columns(columns))


def _parse_excel_config(data: Dict[str, object]) -> Optional[ExcelConfig]:
    fmt = data["format"]
    raw = data.get("excel")
    if fmt == "excel":
        if raw is None:
            raise ValueError(
                "format: excel requires a peer excel: block (with a sheet: subkey)."
            )
        if "sheet" not in raw:
            raise ValueError("excel: block requires a sheet: subkey.")
        return ExcelConfig(sheet=raw["sheet"])
    if raw is not None:
        raise ValueError(
            "excel: block is only valid when format: excel."
        )
    return None


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
