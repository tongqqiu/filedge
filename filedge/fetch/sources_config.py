"""The Sources Config (`sources.yaml`) loader.

A Sources Config is a Fetcher-only file (CONTEXT.md: Sources Config) declaring
how an API Source is pulled — endpoint, incremental cursor, optional credential
lookup, and the staging/state/landing paths. It is deliberately separate from
`pipeline.yaml`, which stays the config for *ingesting* the resulting Files.

This module validates one named source into an in-memory `FetchPlan` and
rejects a malformed config rather than tolerate it. No secret is ever read from
the file: credentials are named by environment variable only.
"""

from dataclasses import dataclass
from typing import Optional

import yaml

from filedge.fetch.errors import SourcesConfigError
from filedge.fetch.source_adapters import (
    EdgarCompanyConceptSource,
    HttpApiSource,
    company_concept_url,
    edgar_cik,
)

SOURCES_CONFIG_VERSION = 1


@dataclass(frozen=True)
class FetchPlan:
    """The validated, resolved plan for pulling one API Source.

    Source-specific HTTP shape lives behind `source`; the plan carries only the
    orchestration paths and settings the Reference Fetcher needs.
    """

    source_name: str
    staging_dir: str
    watched_directory: str
    state_dir: str
    source: HttpApiSource
    gzip: bool = False
    producer: str = "https://github.com/tongqqiu/filedge#reference-fetcher"

    @property
    def source_type(self) -> str:
        return self.source.source_type

    @property
    def cursor_param(self) -> str:
        return self.source.cursor_param

    @property
    def cursor_field(self) -> str:
        return self.source.cursor_field

    @property
    def url(self) -> str:
        return self.source.url

    @property
    def query(self) -> dict:
        return self.source.query

    @property
    def credential_env(self) -> Optional[str]:
        return self.source.credential_env

    @property
    def headers(self) -> dict:
        return self.source.headers

    @property
    def page_size(self) -> int:
        return self.source.page_size

    @property
    def record_path(self) -> Optional[str]:
        return self.source.record_path

    @property
    def cursor_mode(self) -> str:
        if isinstance(self.source, EdgarCompanyConceptSource):
            return "client"
        return "server"

    @property
    def cik(self) -> Optional[str]:
        return getattr(self.source, "cik", None)

    @property
    def taxonomy(self) -> Optional[str]:
        return getattr(self.source, "taxonomy", None)

    @property
    def concept(self) -> Optional[str]:
        return getattr(self.source, "concept", None)

    @property
    def unit(self) -> Optional[str]:
        return getattr(self.source, "unit", None)

    @property
    def user_agent(self) -> Optional[str]:
        return getattr(self.source, "user_agent", None)

    def credential(self) -> Optional[str]:
        return self.source.credential()


_REQUIRED_COMMON = ("name", "type", "staging_dir", "watched_directory", "state_dir")
_REQUIRED_HTTP = _REQUIRED_COMMON + ("url",)
_REQUIRED_EDGAR = _REQUIRED_COMMON + ("cik", "concept", "unit", "user_agent")


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
    source_type = raw.get("type")
    if source_type == "edgar":
        return _parse_edgar_source(raw)
    return _parse_http_source(raw)


def _parse_http_source(raw: dict) -> FetchPlan:
    for key in _REQUIRED_HTTP:
        if not raw.get(key):
            raise SourcesConfigError(f"API Source entry missing required field {key!r}.")

    cursor = _cursor(raw, require_param=True)
    query = _query(raw)
    headers = _headers(raw)

    return FetchPlan(
        source_name=raw["name"],
        staging_dir=raw["staging_dir"],
        watched_directory=raw["watched_directory"],
        state_dir=raw["state_dir"],
        source=HttpApiSource(
            source_name=raw["name"],
            source_type=raw["type"],
            url=raw["url"],
            cursor_param=cursor["param"],
            cursor_field=cursor["field"],
            query=query,
            credential_env=raw.get("credential_env"),
            headers=headers,
            page_param=raw.get("page_param", "page"),
            per_page_param=raw.get("per_page_param", "per_page"),
            page_size=int(raw.get("page_size", 100)),
        ),
        gzip=bool(raw.get("gzip", False)),
        producer=raw.get("producer", FetchPlan.producer),
    )


def _parse_edgar_source(raw: dict) -> FetchPlan:
    for key in _REQUIRED_EDGAR:
        if not raw.get(key):
            raise SourcesConfigError(f"EDGAR Source entry missing required field {key!r}.")

    cursor = _cursor(raw, require_param=False)
    query = _query(raw)
    headers = _headers(raw)
    taxonomy = raw.get("taxonomy", "us-gaap")
    cik = edgar_cik(raw["cik"])
    concept = str(raw["concept"])
    unit = str(raw["unit"])
    user_agent = str(raw["user_agent"])
    headers.setdefault("User-Agent", user_agent)

    return FetchPlan(
        source_name=raw["name"],
        staging_dir=raw["staging_dir"],
        watched_directory=raw["watched_directory"],
        state_dir=raw["state_dir"],
        source=EdgarCompanyConceptSource(
            source_name=raw["name"],
            source_type="edgar",
            url=company_concept_url(cik=cik, taxonomy=taxonomy, concept=concept),
            cursor_param=cursor.get("param", cursor["field"]),
            cursor_field=cursor["field"],
            query=query,
            headers=headers,
            page_size=int(raw.get("page_size", 100)),
            record_path=f"units.{unit}",
            cik=cik,
            taxonomy=taxonomy,
            concept=concept,
            unit=unit,
            user_agent=user_agent,
        ),
        gzip=bool(raw.get("gzip", False)),
        producer=raw.get("producer", FetchPlan.producer),
    )


def _cursor(raw: dict, *, require_param: bool) -> dict:
    cursor = raw.get("cursor")
    if not isinstance(cursor, dict) or not cursor.get("field"):
        raise SourcesConfigError(
            "API Source 'cursor:' must set 'field'."
        )
    if require_param and not cursor.get("param"):
        raise SourcesConfigError(
            "API Source 'cursor:' must set both 'param' and 'field'."
        )
    return cursor


def _query(raw: dict) -> dict:
    query = raw.get("query") or {}
    if not isinstance(query, dict):
        raise SourcesConfigError("API Source 'query:' must be a mapping when present.")
    return query


def _headers(raw: dict) -> dict:
    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        raise SourcesConfigError("API Source 'headers:' must be a mapping when present.")
    return {str(k): str(v) for k, v in headers.items()}
