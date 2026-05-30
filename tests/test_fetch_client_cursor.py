"""Client-side incremental cursor mode (for APIs with no cursor query param, like
EDGAR): fetch one document, emit only records newer than the stored cursor, and
advance to the max. Server-side (GitHub) mode is unchanged.
"""

import json

from filedge.fetch.source_client import HttpSourceClient
from filedge.fetch.source_adapters import EdgarCompanyConceptSource
from filedge.fetch.sources_config import FetchPlan


def _plan(**overrides):
    source_kwargs = dict(
        source_name="edgar",
        source_type="edgar",
        url="https://data.sec.gov/api/xbrl/companyConcept/CIK/us-gaap/Revenues.json",
        cursor_param="since", cursor_field="filed",
        record_path="units.USD",
        cik="0000000001",
        taxonomy="us-gaap",
        concept="Revenues",
        unit="USD",
        user_agent="Filedge Test contact@example.com",
    )
    source_keys = {
        "source_name", "source_type", "url", "cursor_param", "cursor_field", "query",
        "headers", "page_size", "record_path", "cik", "taxonomy", "concept", "unit",
        "user_agent",
    }
    for key in list(overrides):
        if key in source_keys:
            source_kwargs[key] = overrides.pop(key)
    return FetchPlan(
        source_name=source_kwargs["source_name"],
        staging_dir=overrides.pop("staging_dir", "s"),
        watched_directory=overrides.pop("watched_directory", "w"),
        state_dir=overrides.pop("state_dir", "st"),
        source=EdgarCompanyConceptSource(**source_kwargs),
        **overrides,
    )


def _doc(*filed_dates):
    facts = [{"val": i, "filed": d} for i, d in enumerate(filed_dates)]
    return lambda url, headers: (200, {}, json.dumps({"units": {"USD": facts}}).encode())


def test_first_run_emits_all_records():
    client = HttpSourceClient(_doc("2026-01-01", "2026-02-01"), sleep=lambda s: None)
    result = client.fetch(_plan(), None)
    assert [r["filed"] for r in result.records] == ["2026-01-01", "2026-02-01"]
    assert result.next_cursor == "2026-02-01"


def test_only_records_newer_than_cursor_are_emitted():
    client = HttpSourceClient(
        _doc("2026-01-01", "2026-02-01", "2026-03-01"), sleep=lambda s: None
    )
    result = client.fetch(_plan(), "2026-01-01")
    assert [r["filed"] for r in result.records] == ["2026-02-01", "2026-03-01"]
    assert result.next_cursor == "2026-03-01"


def test_records_at_exactly_the_cursor_are_excluded():
    client = HttpSourceClient(_doc("2026-01-01", "2026-02-01"), sleep=lambda s: None)
    result = client.fetch(_plan(), "2026-02-01")
    assert result.records == []
    assert result.next_cursor == "2026-02-01"  # unchanged


def test_no_newer_records_is_a_clean_noop():
    client = HttpSourceClient(_doc("2026-01-01"), sleep=lambda s: None)
    result = client.fetch(_plan(), "2026-05-01")
    assert result.records == []
    assert result.next_cursor == "2026-05-01"


def test_client_mode_fetches_a_single_request_without_cursor_or_page_params():
    seen = {}

    def transport(url, headers):
        seen["url"] = url
        seen["count"] = seen.get("count", 0) + 1
        return 200, {}, json.dumps({"units": {"USD": [{"filed": "2026-01-01"}]}}).encode()

    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_plan(query={}), None)
    assert seen["count"] == 1
    assert "page=" not in seen["url"] and "since=" not in seen["url"]


def test_record_missing_cursor_field_is_excluded_when_cursor_set():
    def transport(url, headers):
        return 200, {}, json.dumps({"units": {"USD": [{"val": 1}]}}).encode()  # no `filed`
    client = HttpSourceClient(transport, sleep=lambda s: None)
    result = client.fetch(_plan(), "2026-01-01")
    assert result.records == []


def test_client_url_includes_static_query_params():
    seen = {}

    def transport(url, headers):
        seen["url"] = url
        return 200, {}, json.dumps({"units": {"USD": []}}).encode()

    client = HttpSourceClient(transport, sleep=lambda s: None)
    client.fetch(_plan(query={"taxonomy": "us-gaap"}), None)
    assert "taxonomy=us-gaap" in seen["url"]
