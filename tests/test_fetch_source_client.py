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


# --- Stripe cursor pagination (cursor_mode="stripe") --------------------------

def _stripe_plan(**overrides):
    kwargs = dict(
        source_name="charges",
        source_type="stripe",
        url="https://api.example/v1/charges",
        staging_dir="s", watched_directory="w", state_dir="st",
        cursor_param="created[gt]", cursor_field="created",
        record_path="data", cursor_mode="stripe", page_size=2,
    )
    kwargs.update(overrides)
    return FetchPlan(**kwargs)


def _stripe_rec(i):
    return {"id": f"ch_{i}", "created": 1700000000 + i}


def _stripe_page(records, has_more):
    body = {"object": "list", "has_more": has_more, "data": records}
    return 200, {}, json.dumps(body).encode()


def test_stripe_paginates_via_starting_after():
    seen = []

    def transport(url, headers):
        q = parse_qs(urlparse(url).query)
        seen.append(q)
        if "starting_after" not in q:
            return _stripe_page([_stripe_rec(1), _stripe_rec(2)], has_more=True)
        return _stripe_page([_stripe_rec(3)], has_more=False)

    client = HttpSourceClient(transport, sleep=lambda s: None)
    result = client.fetch(_stripe_plan(), None)

    assert [r["id"] for r in result.records] == ["ch_1", "ch_2", "ch_3"]
    # The second page is requested with starting_after = last id of the first page.
    assert seen[1]["starting_after"] == ["ch_2"]
    # `limit` is sent on every request.
    assert seen[0]["limit"] == ["2"]
    # Next cursor is the largest cursor_field (created) seen across the window.
    assert result.next_cursor == str(1700000000 + 3)


def test_stripe_stops_on_first_page_when_has_more_false():
    calls = []

    def transport(url, headers):
        calls.append(url)
        return _stripe_page([_stripe_rec(1)], has_more=False)

    client = HttpSourceClient(transport, sleep=lambda s: None)
    result = client.fetch(_stripe_plan(), None)

    assert len(calls) == 1
    assert [r["id"] for r in result.records] == ["ch_1"]


def test_stripe_incremental_cursor_sent_as_created_gt():
    seen = {}

    def transport(url, headers):
        seen["q"] = parse_qs(urlparse(url).query)
        return _stripe_page([], has_more=False)

    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_stripe_plan(), "1700000000")

    assert seen["q"]["created[gt]"] == ["1700000000"]


def test_stripe_empty_data_is_a_clean_noop():
    client = HttpSourceClient(
        lambda url, headers: _stripe_page([], has_more=False), sleep=lambda s: None
    )
    result = client.fetch(_stripe_plan(), "1700000000")

    assert result.records == []
    assert result.next_cursor == "1700000000"  # unchanged


def test_stripe_sends_bearer_credential(monkeypatch):
    seen = {}

    def transport(url, headers):
        seen["auth"] = headers.get("Authorization")
        return _stripe_page([], has_more=False)

    monkeypatch.setenv("STRIPE_KEY", "sk_test_123")
    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_stripe_plan(credential_env="STRIPE_KEY"), None)

    assert seen["auth"] == "Bearer sk_test_123"


def test_stripe_non_object_response_raises():
    client = HttpSourceClient(
        lambda url, headers: (200, {}, json.dumps([1, 2]).encode()), sleep=lambda s: None
    )
    with pytest.raises(SourceClientError, match="JSON object"):
        client.fetch(_stripe_plan(), None)


def test_stripe_non_json_response_raises():
    client = HttpSourceClient(
        lambda url, headers: (200, {}, b"not json at all"), sleep=lambda s: None
    )
    with pytest.raises(SourceClientError, match="Non-JSON"):
        client.fetch(_stripe_plan(), None)


def test_stripe_non_list_data_raises():
    body = json.dumps({"object": "list", "has_more": False, "data": {"id": "x"}}).encode()
    client = HttpSourceClient(lambda url, headers: (200, {}, body), sleep=lambda s: None)
    with pytest.raises(SourceClientError, match="Expected a JSON array"):
        client.fetch(_stripe_plan(), None)


def test_stripe_stops_when_last_record_has_no_id():
    # has_more is true, but the last record carries no id to page on — stop rather
    # than loop forever.
    calls = []

    def transport(url, headers):
        calls.append(url)
        return _stripe_page([{"created": 1700000001}], has_more=True)

    client = HttpSourceClient(transport, sleep=lambda s: None)
    result = client.fetch(_stripe_plan(), None)

    assert len(calls) == 1
    assert result.records == [{"created": 1700000001}]
