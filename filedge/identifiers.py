import re
from typing import Iterable

from filedge.config import PipelineConfig


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class IdentifierError(ValueError):
    pass


def validate_identifier(name: str, *, kind: str) -> None:
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise IdentifierError(
            f"Invalid {kind} identifier {name!r}. "
            "Use letters, numbers, and underscores; identifiers must not start with a number."
        )


def validate_pipeline_identifiers(config: PipelineConfig) -> None:
    validate_identifier(config.dest_table, kind="destination table")
    for col in config.columns:
        validate_identifier(col.dest, kind=f"destination column for source {col.source!r}")


def quote_identifier(name: str) -> str:
    validate_identifier(name, kind="SQL")
    return f'"{name}"'


def quote_identifiers(names: Iterable[str]) -> list[str]:
    return [quote_identifier(name) for name in names]
