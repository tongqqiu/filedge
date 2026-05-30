"""The Sources Config (`sources.yaml`) loader.

A Sources Config is a Fetcher-only file (CONTEXT.md: Sources Config) declaring
how an API Source is pulled — endpoint, incremental cursor, optional credential
lookup, and the staging/state/landing paths. It is deliberately separate from
`pipeline.yaml`, which stays the config for *ingesting* the resulting Files.

This module validates one named source into an in-memory `FetchPlan` and
rejects a malformed config rather than tolerate it. No secret is ever read from
the file: credentials are named by environment variable only.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

from filedge.fetch.errors import SourcesConfigError

SOURCES_CONFIG_VERSION = 1


@dataclass(frozen=True)
class FetchPlan:
    """The validated, resolved plan for pulling one API Source.

    `cursor_field` is a dotted path into each record (e.g.
    ``commit.committer.date``) from which the next cursor is derived; the
    largest value seen in a window becomes the next run's `cursor_param` value.
    """

    source_name: str
    source_type: str
    url: str
    staging_dir: str
    watched_directory: str
    state_dir: str
    cursor_param: str
    cursor_field: str
    query: dict = field(default_factory=dict)
    credential_env: Optional[str] = None
    page_param: str = "page"
    per_page_param: str = "per_page"
    page_size: int = 100
    gzip: bool = False
    producer: str = "https://github.com/tongqqiu/filedge#reference-fetcher"
    # When set, records are extracted from this dotted path inside a JSON-object
    # response (e.g. EDGAR `units.USD` or `data`). None → the response is a
    # top-level JSON array (the GitHub default).
    record_path: Optional[str] = None
    # "server" (default): the cursor is sent as a query param and the API
    # returns only newer records (GitHub). "client": the API has no cursor
    # param, so a single document is fetched and records are filtered by
    # cursor_field locally (EDGAR).
    cursor_mode: str = "server"

    def credential(self) -> Optional[str]:
        """Resolve the bearer credential from the environment, if configured."""
        if not self.credential_env:
            return None
        return os.environ.get(self.credential_env)


_REQUIRED = ("name", "type", "url", "staging_dir", "watched_directory", "state_dir")


def load_sources(config_path: str, source_name: str) -> FetchPlan:
    """Load `sources.yaml` and return the `FetchPlan` for `source_name`."""
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise SourcesConfigError(f"Sources Config {config_path!r} not found.") from e

    if not isinstance(data, dict):
        raise SourcesConfigError("Sources Config must be a mapping.")
    version = data.get("version")
    if version != SOURCES_CONFIG_VERSION:
        raise SourcesConfigError(
            f"Unsupported Sources Config version {version!r}; "
            f"expected {SOURCES_CONFIG_VERSION}."
        )
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise SourcesConfigError("Sources Config must have a non-empty 'sources:' list.")

    matches = [s for s in raw_sources if isinstance(s, dict) and s.get("name") == source_name]
    if not matches:
        known = ", ".join(
            repr(s.get("name")) for s in raw_sources if isinstance(s, dict)
        ) or "(none)"
        raise SourcesConfigError(
            f"No API Source {source_name!r} in {config_path!r}. Known: {known}."
        )
    if len(matches) > 1:
        raise SourcesConfigError(f"Duplicate API Source {source_name!r} in {config_path!r}.")

    return _parse_source(matches[0])


def _parse_source(raw: dict) -> FetchPlan:
    for key in _REQUIRED:
        if not raw.get(key):
            raise SourcesConfigError(f"API Source entry missing required field {key!r}.")

    cursor = raw.get("cursor")
    if not isinstance(cursor, dict) or not cursor.get("param") or not cursor.get("field"):
        raise SourcesConfigError(
            "API Source 'cursor:' must set both 'param' and 'field'."
        )

    query = raw.get("query") or {}
    if not isinstance(query, dict):
        raise SourcesConfigError("API Source 'query:' must be a mapping when present.")

    return FetchPlan(
        source_name=raw["name"],
        source_type=raw["type"],
        url=raw["url"],
        staging_dir=raw["staging_dir"],
        watched_directory=raw["watched_directory"],
        state_dir=raw["state_dir"],
        cursor_param=cursor["param"],
        cursor_field=cursor["field"],
        query=query,
        credential_env=raw.get("credential_env"),
        page_param=raw.get("page_param", "page"),
        per_page_param=raw.get("per_page_param", "per_page"),
        page_size=int(raw.get("page_size", 100)),
        gzip=bool(raw.get("gzip", False)),
        producer=raw.get("producer", FetchPlan.producer),
    )
