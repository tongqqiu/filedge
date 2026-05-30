"""API Source adapters for the Reference Fetcher.

Each adapter owns source-specific HTTP shape: URL construction, pagination,
record extraction, cursor advancement, and Source Manifest range metadata. The
Fetcher orchestration only needs the common plan fields plus this interface.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote, urlencode

from filedge.fetch.errors import SourcesConfigError


@dataclass(frozen=True)
class HttpApiSource:
    """A paginated HTTP API Source with server-side cursor filtering."""

    source_name: str
    source_type: str
    url: str
    cursor_param: str
    cursor_field: str
    query: dict = field(default_factory=dict)
    credential_env: Optional[str] = None
    headers: dict = field(default_factory=dict)
    page_param: str = "page"
    per_page_param: str = "per_page"
    page_size: int = 100
    record_path: Optional[str] = None

    def credential(self) -> Optional[str]:
        """Resolve the bearer credential from the environment, if configured."""
        if not self.credential_env:
            return None
        return os.environ.get(self.credential_env)

    def fetch_records(self, client, cursor: Optional[str]) -> List[dict]:
        """Fetch the cursor window through the provided HTTP client."""
        records: List[dict] = []
        page = 1
        while True:
            batch = client.request_records(
                self._server_url(cursor, page),
                headers=self.request_headers(),
                record_path=self.record_path,
            )
            if not batch:
                break
            records.extend(batch)
            if len(batch) < self.page_size:
                break
            page += 1
        return records

    def next_cursor(self, records: List[dict], cursor: Optional[str]) -> Optional[str]:
        """Return the next high-water mark for this source."""
        best = cursor
        for record in records:
            value = dotted_get(record, self.cursor_field)
            if value is None:
                continue
            value = str(value)
            if best is None or value > best:
                best = value
        return best

    def source_range(
        self, from_cursor: Optional[str], to_cursor: Optional[str]
    ) -> dict:
        """Source Manifest range metadata for this source."""
        return {
            "cursor_param": self.cursor_param,
            "from": from_cursor,
            "to": to_cursor,
        }

    def request_headers(self) -> dict:
        """Headers sent on each request, including bearer auth when configured."""
        headers = {"Accept": "application/json", **self.headers}
        credential = self.credential()
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _server_url(self, cursor: Optional[str], page: int) -> str:
        params = dict(self.query)
        if cursor:
            params[self.cursor_param] = cursor
        params[self.page_param] = page
        params[self.per_page_param] = self.page_size
        return f"{self.url}?{urlencode(params)}"


@dataclass(frozen=True)
class EdgarCompanyConceptSource(HttpApiSource):
    """EDGAR companyConcept adapter with client-side cursor filtering."""

    cik: str = ""
    taxonomy: str = "us-gaap"
    concept: str = ""
    unit: str = ""
    user_agent: str = ""

    def fetch_records(self, client, cursor: Optional[str]) -> List[dict]:
        records = client.request_records(
            self._client_url(),
            headers=self.request_headers(),
            record_path=self.record_path,
        )
        return [r for r in records if self._is_newer(r, cursor)]

    def source_range(
        self, from_cursor: Optional[str], to_cursor: Optional[str]
    ) -> dict:
        return {
            "cursor_param": self.cursor_param,
            "cursor_field": self.cursor_field,
            "from": from_cursor,
            "to": to_cursor,
            "cik": self.cik,
            "taxonomy": self.taxonomy,
            "concept": self.concept,
            "unit": self.unit,
        }

    def _client_url(self) -> str:
        if not self.query:
            return self.url
        return f"{self.url}?{urlencode(self.query)}"

    def _is_newer(self, record: dict, cursor: Optional[str]) -> bool:
        if cursor is None:
            return True
        value = dotted_get(record, self.cursor_field)
        return value is not None and str(value) > cursor


def company_concept_url(*, cik: str, taxonomy: str, concept: str) -> str:
    """Build the SEC EDGAR companyConcept endpoint URL."""
    padded_cik = edgar_cik(cik)
    return (
        "https://data.sec.gov/api/xbrl/companyconcept/"
        f"CIK{padded_cik}/{quote(str(taxonomy), safe='')}/"
        f"{quote(str(concept), safe='')}.json"
    )


def edgar_cik(value) -> str:
    digits = str(value)
    if not digits.isdigit():
        raise SourcesConfigError("EDGAR Source 'cik' must contain only digits.")
    if len(digits) > 10:
        raise SourcesConfigError("EDGAR Source 'cik' must be at most 10 digits.")
    return digits.zfill(10)


def dotted_get(record: dict, dotted: str):
    """Resolve a dotted path into a nested record."""
    current = record
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
