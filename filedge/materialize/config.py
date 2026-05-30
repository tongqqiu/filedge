"""Load a Queue Source from the Sources Config (`sources.yaml`).

The Reference Queue Materializer reads the *same* `sources.yaml` the Reference
Fetcher does (CONTEXT.md: Sources Config), but parses `type: kafka` entries.
The Fetcher's loader owns http-typed entries; this one owns kafka-typed entries.
A malformed entry is rejected rather than tolerated.

Credentials (SASL/TLS) are named by reference only — `env:NAME` or
`secrets:/abs/path`, resolved at consume time through the same seam the rest of
Filedge uses (`filedge.reference`). No secret is ever read from the file.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

from filedge.materialize.errors import MaterializeConfigError
from filedge.reference import ReferenceError, resolve_reference

SOURCES_CONFIG_VERSION = 1
_TRIGGERS = ("drain", "continuous")
_REQUIRED = (
    "name", "type", "brokers", "topic", "consumer_group",
    "staging_dir", "watched_directory", "state_dir",
)


@dataclass(frozen=True)
class MaterializePlan:
    """The validated plan for materializing one Kafka Queue Source.

    `credentials` maps a logical name (e.g. ``sasl_password``) to an
    `env:`/`secrets:` reference; `credential()` resolves one on demand.
    """

    source_name: str
    source_type: str
    brokers: List[str]
    topic: str
    consumer_group: str
    staging_dir: str
    watched_directory: str
    state_dir: str
    batch_size: int = 1000
    batch_timeout_seconds: float = 30.0
    trigger: str = "drain"
    decode_format: str = "json"
    gzip: bool = False
    producer: str = "https://github.com/tongqqiu/filedge#reference-materializer"
    # Plain (non-secret) Kafka security settings; the credentials below are
    # env/secrets references resolved on demand.
    security_protocol: Optional[str] = None
    sasl_mechanism: Optional[str] = None
    credentials: Dict[str, str] = field(default_factory=dict)

    def credential(self, name: str) -> Optional[str]:
        """Resolve a named credential reference to its value, or None if unset."""
        ref = self.credentials.get(name)
        if not ref:
            return None
        try:
            return resolve_reference(ref, usage=f"kafka credential {name!r}")
        except ReferenceError as e:
            raise MaterializeConfigError(str(e)) from e


def load_kafka_source(config_path: str, source_name: str) -> MaterializePlan:
    """Load `sources.yaml` and return the `MaterializePlan` for a kafka source."""
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise MaterializeConfigError(f"Sources Config {config_path!r} not found.") from e

    if not isinstance(data, dict):
        raise MaterializeConfigError("Sources Config must be a mapping.")
    if data.get("version") != SOURCES_CONFIG_VERSION:
        raise MaterializeConfigError(
            f"Unsupported Sources Config version {data.get('version')!r}; "
            f"expected {SOURCES_CONFIG_VERSION}."
        )
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise MaterializeConfigError(
            "Sources Config must have a non-empty 'sources:' list."
        )

    matches = [
        s for s in raw_sources if isinstance(s, dict) and s.get("name") == source_name
    ]
    if not matches:
        known = ", ".join(
            repr(s.get("name")) for s in raw_sources if isinstance(s, dict)
        ) or "(none)"
        raise MaterializeConfigError(
            f"No source {source_name!r} in {config_path!r}. Known: {known}."
        )
    if len(matches) > 1:
        raise MaterializeConfigError(f"Duplicate source {source_name!r} in {config_path!r}.")

    return _parse_kafka_source(matches[0])


def _parse_kafka_source(raw: dict) -> MaterializePlan:
    if raw.get("type") != "kafka":
        raise MaterializeConfigError(
            f"Source {raw.get('name')!r} is type {raw.get('type')!r}, not 'kafka'; "
            "filedge-materialize only handles kafka sources."
        )
    for key in _REQUIRED:
        if not raw.get(key):
            raise MaterializeConfigError(
                f"Queue Source entry missing required field {key!r}."
            )

    trigger = raw.get("trigger", "drain")
    if trigger not in _TRIGGERS:
        raise MaterializeConfigError(
            f"trigger must be one of {_TRIGGERS}, got {trigger!r}."
        )

    credentials = raw.get("credentials") or {}
    if not isinstance(credentials, dict):
        raise MaterializeConfigError("'credentials:' must be a mapping when present.")

    return MaterializePlan(
        source_name=raw["name"],
        source_type=raw["type"],
        brokers=_as_broker_list(raw["brokers"]),
        topic=raw["topic"],
        consumer_group=raw["consumer_group"],
        staging_dir=raw["staging_dir"],
        watched_directory=raw["watched_directory"],
        state_dir=raw["state_dir"],
        batch_size=int(raw.get("batch_size", 1000)),
        batch_timeout_seconds=float(raw.get("batch_timeout_seconds", 30)),
        trigger=trigger,
        decode_format=raw.get("format", "json"),
        gzip=bool(raw.get("gzip", False)),
        producer=raw.get("producer", MaterializePlan.producer),
        security_protocol=raw.get("security_protocol"),
        sasl_mechanism=raw.get("sasl_mechanism"),
        credentials=dict(credentials),
    )


def _as_broker_list(value) -> List[str]:
    """Accept brokers as a list or a comma-separated string; normalize to a list."""
    if isinstance(value, list):
        return [str(v) for v in value]
    return [part.strip() for part in str(value).split(",") if part.strip()]
