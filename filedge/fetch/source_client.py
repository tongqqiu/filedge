"""HTTP JSON transport for API Source adapters.

Owns the source-neutral HTTP concerns ADR-0006 assigns to the Fetcher:
transport, JSON decoding, record extraction, and rate-limit retry. API Source
adapters own URL shape, pagination, cursor advancement, and source-specific
manifest metadata.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from filedge.fetch.errors import SourceClientError
from filedge.fetch.source_adapters import dotted_get

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
    """Run an API Source adapter over a retrying HTTP JSON request interface."""

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

    def fetch(self, plan, cursor: Optional[str]) -> FetchResult:
        started_at = self._now()
        records = plan.source.fetch_records(self, cursor)
        finished_at = self._now()
        return FetchResult(
            records=records,
            next_cursor=plan.source.next_cursor(records, cursor),
            started_at=started_at,
            finished_at=finished_at,
        )

    def request_records(
        self, url: str, *, headers: dict, record_path: Optional[str]
    ) -> List[dict]:
        """Fetch one JSON response and extract its records."""
        for attempt in range(self._max_retries + 1):
            status, resp_headers, body = self._transport(url, headers)
            if status == 200:
                return extract_records(body, url, record_path)
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

    located = dotted_get(payload, record_path) if isinstance(payload, dict) else None
    if located is None:
        return []
    if not isinstance(located, list):
        raise SourceClientError(
            f"Expected a JSON array at {record_path!r} in {url!r}, "
            f"got {type(located).__name__}."
        )
    return located
