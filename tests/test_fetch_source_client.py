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
