"""The page iterator, driven by a fake transport (no network): it walks pages
until a short page, derives the next cursor from records, and backs off then
retries on a rate-limit response.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from filedge.fetch.errors import SourceClientError
from filedge.fetch.source_client import HttpSourceClient
from filedge.fetch.sources_config import FetchPlan


def _plan(page_size=2, **overrides):
    kwargs = dict(
        source_name="commits",
        source_type="github",
        url="https://api.example/commits",
        staging_dir="s", watched_directory="w", state_dir="st",
        cursor_param="since", cursor_field="updated_at",
        page_size=page_size,
    )
    kwargs.update(overrides)
    return FetchPlan(**kwargs)


def _rec(i):
    return {"id": i, "updated_at": f"2026-05-{i:02d}"}


def _ok(records):
    return 200, {}, json.dumps(records).encode()


def test_paginates_until_a_short_page(tmp_path):
    pages = {1: [_rec(1), _rec(2)], 2: [_rec(3)]}  # page_size 2; page 2 is short

    def transport(url, headers):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        return _ok(pages.get(page, []))

    client = HttpSourceClient(transport, sleep=lambda s: None)
    result = client.fetch(_plan(page_size=2), None)

    assert [r["id"] for r in result.records] == [1, 2, 3]
    assert result.next_cursor == "2026-05-03"  # max cursor_field across the window


def test_empty_first_page_is_a_clean_noop():
    client = HttpSourceClient(lambda url, headers: _ok([]), sleep=lambda s: None)
    result = client.fetch(_plan(), "2026-05-10")
    assert result.records == []
    assert result.next_cursor == "2026-05-10"  # unchanged


def test_cursor_is_passed_as_a_query_param_when_present():
    seen = {}

    def transport(url, headers):
        seen["url"] = url
        return _ok([])

    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_plan(), "2026-05-10")

    q = parse_qs(urlparse(seen["url"]).query)
    assert q["since"] == ["2026-05-10"]


def test_backs_off_then_retries_on_rate_limit():
    calls = {"n": 0}

    def transport(url, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {"Retry-After": "0"}, b""
        return _ok([_rec(1)])  # short page (< page_size) -> stop

    slept = []
    client = HttpSourceClient(transport, sleep=slept.append, max_retries=3)
    result = client.fetch(_plan(page_size=100), None)

    assert calls["n"] == 2
    assert slept == [0.0]
    assert [r["id"] for r in result.records] == [1]


def test_gives_up_after_max_retries_on_persistent_rate_limit():
    def transport(url, headers):
        return 429, {"Retry-After": "0"}, b""

    client = HttpSourceClient(transport, sleep=lambda s: None, max_retries=2)
    with pytest.raises(SourceClientError, match="Rate limited"):
        client.fetch(_plan(), None)


def test_non_200_non_rate_limit_status_raises():
    client = HttpSourceClient(lambda url, headers: (500, {}, b""), sleep=lambda s: None)
    with pytest.raises(SourceClientError, match="HTTP 500"):
        client.fetch(_plan(), None)


def test_non_array_response_raises():
    client = HttpSourceClient(
        lambda url, headers: (200, {}, b'{"not": "an array"}'), sleep=lambda s: None
    )
    with pytest.raises(SourceClientError, match="array"):
        client.fetch(_plan(), None)


def test_non_json_response_raises():
    client = HttpSourceClient(
        lambda url, headers: (200, {}, b"not json at all"), sleep=lambda s: None
    )
    with pytest.raises(SourceClientError, match="Non-JSON"):
        client.fetch(_plan(), None)


def test_credential_is_sent_as_bearer_header(monkeypatch):
    seen = {}

    def transport(url, headers):
        seen["auth"] = headers.get("Authorization")
        return _ok([])

    monkeypatch.setenv("TOK", "secret-token")
    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_plan(credential_env="TOK"), None)

    assert seen["auth"] == "Bearer secret-token"


def test_static_headers_are_sent_with_each_request():
    seen = []

    def transport(url, headers):
        seen.append(dict(headers))
        return _ok([])

    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_plan(headers={"User-Agent": "Filedge Test contact@example.com"}), None)

    assert seen[0]["Accept"] == "application/json"
    assert seen[0]["User-Agent"] == "Filedge Test contact@example.com"


def test_bearer_authorization_composes_with_static_headers(monkeypatch):
    seen = {}

    def transport(url, headers):
        seen.update(headers)
        return _ok([])

    monkeypatch.setenv("TOK", "secret-token")
    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(
        _plan(
            credential_env="TOK",
            headers={"User-Agent": "Filedge Test contact@example.com"},
        ),
        None,
    )

    assert seen["Authorization"] == "Bearer secret-token"
    assert seen["User-Agent"] == "Filedge Test contact@example.com"


def test_rate_limit_detected_via_remaining_header_then_succeeds():
    calls = {"n": 0}

    def transport(url, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            # No Retry-After; exhausted quota signalled by remaining == "0".
            return 403, {"X-RateLimit-Remaining": "0"}, b""
        return _ok([_rec(1)])

    slept = []
    client = HttpSourceClient(transport, sleep=slept.append, backoff_seconds=2.0)
    result = client.fetch(_plan(page_size=100), None)

    assert calls["n"] == 2
    assert slept == [2.0]  # fell back to backoff_seconds (no Retry-After header)
    assert [r["id"] for r in result.records] == [1]


def test_non_numeric_retry_after_falls_back_to_backoff():
    calls = {"n": 0}

    def transport(url, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {"Retry-After": "soon"}, b""  # unparseable
        return _ok([_rec(1)])

    slept = []
    client = HttpSourceClient(transport, sleep=slept.append, backoff_seconds=1.5)
    client.fetch(_plan(page_size=100), None)

    assert slept == [1.5]


def test_records_without_cursor_field_leave_cursor_unchanged():
    client = HttpSourceClient(
        lambda url, headers: _ok([{"id": 1}, {"id": 2}]), sleep=lambda s: None
    )
    result = client.fetch(_plan(page_size=100), "2026-05-05")
    assert result.next_cursor == "2026-05-05"  # no cursor_field present in records


def test_403_without_rate_limit_signal_raises():
    client = HttpSourceClient(lambda url, headers: (403, {}, b""), sleep=lambda s: None)
    with pytest.raises(SourceClientError, match="HTTP 403"):
        client.fetch(_plan(), None)


# --- urllib_transport (the default stdlib transport) ---

class _FakeResp:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_urllib_transport_returns_status_headers_body(monkeypatch):
    from filedge.fetch import source_client as sc

    monkeypatch.setattr(
        sc.urllib.request, "urlopen",
        lambda req: _FakeResp(200, {"X-RateLimit-Remaining": "59"}, b"[]"),
    )
    status, headers, body = sc.urllib_transport("https://api.example/x", {})
    assert status == 200
    assert headers["X-RateLimit-Remaining"] == "59"
    assert body == b"[]"


def test_urllib_transport_maps_http_error_to_status(monkeypatch):
    import io
    import urllib.error

    from filedge.fetch import source_client as sc

    def raise_http_error(req):
        raise urllib.error.HTTPError(
            "https://api.example/x", 429, "Too Many", {"Retry-After": "1"},
            io.BytesIO(b"slow down"),
        )

    monkeypatch.setattr(sc.urllib.request, "urlopen", raise_http_error)
    status, headers, body = sc.urllib_transport("https://api.example/x", {})
    assert status == 429
    assert headers["Retry-After"] == "1"
    assert body == b"slow down"


def test_urllib_transport_url_error_raises_source_client_error(monkeypatch):
    import urllib.error

    from filedge.fetch import source_client as sc

    def raise_url_error(req):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(sc.urllib.request, "urlopen", raise_url_error)
    with pytest.raises(SourceClientError, match="Cannot reach"):
        sc.urllib_transport("https://api.example/x", {})
