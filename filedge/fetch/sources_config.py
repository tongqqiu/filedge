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
from urllib.parse import quote

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
    headers: dict = field(default_factory=dict)
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
    cik: Optional[str] = None
    taxonomy: Optional[str] = None
    concept: Optional[str] = None
    unit: Optional[str] = None
    user_agent: Optional[str] = None

    def credential(self) -> Optional[str]:
        """Resolve the bearer credential from the environment, if configured."""
        if not self.credential_env:
            return None
        return os.environ.get(self.credential_env)


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
        source_type=raw["type"],
        url=raw["url"],
        staging_dir=raw["staging_dir"],
        watched_directory=raw["watched_directory"],
        state_dir=raw["state_dir"],
        cursor_param=cursor["param"],
        cursor_field=cursor["field"],
        query=query,
        credential_env=raw.get("credential_env"),
        headers=headers,
        page_param=raw.get("page_param", "page"),
        per_page_param=raw.get("per_page_param", "per_page"),
        page_size=int(raw.get("page_size", 100)),
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
    cik = _edgar_cik(raw["cik"])
    concept = str(raw["concept"])
    unit = str(raw["unit"])
    user_agent = str(raw["user_agent"])
    headers.setdefault("User-Agent", user_agent)

    return FetchPlan(
        source_name=raw["name"],
        source_type="edgar",
        url=company_concept_url(cik=cik, taxonomy=taxonomy, concept=concept),
        staging_dir=raw["staging_dir"],
        watched_directory=raw["watched_directory"],
        state_dir=raw["state_dir"],
        cursor_param=cursor.get("param", cursor["field"]),
        cursor_field=cursor["field"],
        query=query,
        headers=headers,
        page_size=int(raw.get("page_size", 100)),
        gzip=bool(raw.get("gzip", False)),
        producer=raw.get("producer", FetchPlan.producer),
        record_path=f"units.{unit}",
        cursor_mode="client",
        cik=cik,
        taxonomy=taxonomy,
        concept=concept,
        unit=unit,
        user_agent=user_agent,
    )


def company_concept_url(*, cik: str, taxonomy: str, concept: str) -> str:
    """Build the SEC EDGAR companyConcept endpoint URL."""
    padded_cik = _edgar_cik(cik)
    return (
        "https://data.sec.gov/api/xbrl/companyconcept/"
        f"CIK{padded_cik}/{quote(str(taxonomy), safe='')}/"
        f"{quote(str(concept), safe='')}.json"
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


def _edgar_cik(value) -> str:
    digits = str(value)
    if not digits.isdigit():
        raise SourcesConfigError("EDGAR Source 'cik' must contain only digits.")
    if len(digits) > 10:
        raise SourcesConfigError("EDGAR Source 'cik' must be at most 10 digits.")
    return digits.zfill(10)
