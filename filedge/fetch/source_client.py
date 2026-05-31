"""The API Source client — page through an HTTP JSON API.

Owns the API-specific concerns ADR-0006 assigns to the Fetcher: pagination and
rate-limit handling. The HTTP transport is injectable, so the page iterator is
fully testable against a fake without a network. The reference targets an open,
no-auth JSON API (e.g. the GitHub REST API's commits/issues, which exercises
both page-based pagination and a `since`-style cursor); a fintech API is a new
transport/config, not a rewrite.

The cursor model: records are pulled in `cursor` order; the largest value of the
plan's `cursor_field` seen in a window becomes the next run's cursor. Returning
zero records is a valid no-op — the caller advances nothing.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlencode

from filedge.fetch.errors import SourceClientError
from filedge.fetch.sources_config import FetchPlan

# (status_code, response_headers, body_bytes)
Transport = Callable[[str, dict], Tuple[int, dict, bytes]]

_RATE_LIMIT_STATUSES = (403, 429)


@dataclass(frozen=True)
class FetchResult:
    records: List[dict]
    next_cursor: Optional[str]
    started_at: str
    finished_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def urllib_transport(url: str, headers: dict) -> Tuple[int, dict, bytes]:
    """Default transport over stdlib urllib — no third-party dependency."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except urllib.error.URLError as e:
        raise SourceClientError(f"Cannot reach {url!r}: {e.reason}") from e


class HttpSourceClient:
    """Fetch all records for a cursor window, across pages, rate-limit aware."""

    def __init__(
        self,
        transport: Transport = urllib_transport,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], str] = _utc_now_iso,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ):
        self._transport = transport
        self._sleep = sleep
        self._now = now
        self._max_retries = max_retries
        self._backoff = backoff_seconds

    def fetch(self, plan: FetchPlan, cursor: Optional[str]) -> FetchResult:
        started_at = self._now()
        if plan.cursor_mode == "client":
            records = self._fetch_client(plan, cursor)
        elif plan.cursor_mode == "stripe":
            records = self._fetch_stripe(plan, cursor)
        else:
            records = self._fetch_server(plan, cursor)
        finished_at = self._now()
        return FetchResult(
            records=records,
            next_cursor=self._max_cursor(plan, records, cursor),
            started_at=started_at,
            finished_at=finished_at,
        )

    def _fetch_server(self, plan: FetchPlan, cursor: Optional[str]) -> List[dict]:
        """Paginated fetch where the API filters by the cursor query param (GitHub)."""
        records: List[dict] = []
        page = 1
        while True:
            batch = self._request(plan, self._server_url(plan, cursor, page))
            if not batch:
                break
            records.extend(batch)
            if len(batch) < plan.page_size:
                break
            page += 1
        return records

    def _fetch_client(self, plan: FetchPlan, cursor: Optional[str]) -> List[dict]:
        """Single-document fetch; filter to records newer than the cursor locally (EDGAR)."""
        records = self._request(plan, self._client_url(plan))
        return [r for r in records if self._is_newer(plan, r, cursor)]

    def _fetch_stripe(self, plan: FetchPlan, cursor: Optional[str]) -> List[dict]:
        """Cursor-paginated fetch (Stripe): walk ``starting_after`` while the list's
        ``has_more`` is true, taking records from the ``data`` array. The cursor
        param (default ``created[gt]``) filters incrementally on the server; the
        next run's cursor is the largest ``cursor_field`` seen, as for every mode.
        """
        records: List[dict] = []
        starting_after: Optional[str] = None
        while True:
            payload = self._request_object(plan, self._stripe_url(plan, cursor, starting_after))
            batch = payload.get(plan.record_path or "data") or []
            if not isinstance(batch, list):
                raise SourceClientError(
                    f"Expected a JSON array at {plan.record_path!r} in {plan.url!r}."
                )
            records.extend(batch)
            if not batch or not payload.get("has_more"):
                break
            last = batch[-1]
            starting_after = last.get("id") if isinstance(last, dict) else None
            if not starting_after:
                break
        return records

    def _get(self, plan: FetchPlan, url: str) -> bytes:
        """One rate-limit-aware GET; returns the 200 body or raises SourceClientError."""
        headers = {"Accept": "application/json", **plan.headers}
        credential = plan.credential()
        if credential:
            headers["Authorization"] = f"Bearer {credential}"

        for attempt in range(self._max_retries + 1):
            status, resp_headers, body = self._transport(url, headers)
            if status == 200:
                return body
            if status in _RATE_LIMIT_STATUSES and self._is_rate_limited(resp_headers):
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay(resp_headers))
                    continue
                raise SourceClientError(
                    f"Rate limited fetching {url!r} after {self._max_retries} retries."
                )
            raise SourceClientError(f"HTTP {status} fetching {url!r}.")
        raise SourceClientError(  # pragma: no cover - loop always returns or raises
            f"Exhausted retries fetching {url!r}."
        )

    def _request(self, plan: FetchPlan, url: str) -> List[dict]:
        return extract_records(self._get(plan, url), url, plan.record_path)

    def _request_object(self, plan: FetchPlan, url: str) -> dict:
        """GET a JSON object response (Stripe list envelope), not a bare array."""
        body = self._get(plan, url)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise SourceClientError(f"Non-JSON response from {url!r}.") from e
        if not isinstance(payload, dict):
            raise SourceClientError(
                f"Expected a JSON object from {url!r}, got {type(payload).__name__}."
            )
        return payload

    def _stripe_url(
        self, plan: FetchPlan, cursor: Optional[str], starting_after: Optional[str]
    ) -> str:
        params = dict(plan.query)
        params["limit"] = plan.page_size
        if cursor:
            params[plan.cursor_param] = cursor
        if starting_after:
            params["starting_after"] = starting_after
        return f"{plan.url}?{urlencode(params)}"

    def _server_url(self, plan: FetchPlan, cursor: Optional[str], page: int) -> str:
        params = dict(plan.query)
        if cursor:
            params[plan.cursor_param] = cursor
        params[plan.page_param] = page
        params[plan.per_page_param] = plan.page_size
        return f"{plan.url}?{urlencode(params)}"

    def _client_url(self, plan: FetchPlan) -> str:
        if not plan.query:
            return plan.url
        return f"{plan.url}?{urlencode(plan.query)}"

    def _is_newer(self, plan: FetchPlan, record: dict, cursor: Optional[str]) -> bool:
        """True if the record's cursor_field is strictly greater than the cursor.

        A first run (no cursor) keeps every record; a record missing the
        cursor_field is excluded (it cannot be ordered).
        """
        if cursor is None:
            return True
        value = _dotted_get(record, plan.cursor_field)
        return value is not None and str(value) > cursor

    def _is_rate_limited(self, headers: dict) -> bool:
        if "Retry-After" in headers:
            return True
        remaining = headers.get("X-RateLimit-Remaining")
        return remaining == "0"

    def _retry_delay(self, headers: dict) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self._backoff

    def _max_cursor(
        self, plan: FetchPlan, records: List[dict], cursor: Optional[str]
    ) -> Optional[str]:
        best = cursor
        for record in records:
            value = _dotted_get(record, plan.cursor_field)
            if value is None:
                continue
            value = str(value)
            if best is None or value > best:
                best = value
        return best


def extract_records(body: bytes, url: str, record_path: Optional[str] = None) -> List[dict]:
    """Extract the record list from a JSON response.

    With ``record_path`` None the response must be a top-level JSON array (the
    GitHub default). With a dotted ``record_path`` the records are the array at
    that path inside a JSON object (e.g. EDGAR ``units.USD`` or ``data``); a
    missing path or empty document yields an empty list rather than erroring.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise SourceClientError(f"Non-JSON response from {url!r}.") from e

    if record_path is None:
        if not isinstance(payload, list):
            raise SourceClientError(
                f"Expected a JSON array from {url!r}, got {type(payload).__name__}."
            )
        return payload

    located = _dotted_get(payload, record_path) if isinstance(payload, dict) else None
    if located is None:
        return []
    if not isinstance(located, list):
        raise SourceClientError(
            f"Expected a JSON array at {record_path!r} in {url!r}, "
            f"got {type(located).__name__}."
        )
    return located


def _dotted_get(record: dict, dotted: str):
    """Resolve a dotted path (`commit.committer.date`) into a nested record."""
    current = record
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
