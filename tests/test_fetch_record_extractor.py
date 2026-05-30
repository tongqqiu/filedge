"""extract_records pulls the record list from array, nested-path, and units-style
JSON responses; missing/empty yields an empty list. The GitHub (top-level array)
default is unchanged.
"""

import json

import pytest

from filedge.fetch.errors import SourceClientError
from filedge.fetch.source_client import extract_records


def _b(obj):
    return json.dumps(obj).encode()


def test_top_level_array_returned_as_is():
    assert extract_records(_b([{"id": 1}, {"id": 2}]), "u") == [{"id": 1}, {"id": 2}]


def test_nested_dotted_path_array():
    body = _b({"data": [{"cik": 1}, {"cik": 2}], "meta": {}})
    assert extract_records(body, "u", "data") == [{"cik": 1}, {"cik": 2}]


def test_units_style_nested_list():
    # EDGAR companyConcept: units.USD is already a list of fact objects.
    body = _b({"units": {"USD": [{"val": 10, "filed": "2026-01-01"}]}})
    assert extract_records(body, "u", "units.USD") == [{"val": 10, "filed": "2026-01-01"}]


def test_missing_path_yields_empty_list():
    assert extract_records(_b({"units": {"EUR": []}}), "u", "units.USD") == []


def test_empty_object_yields_empty_list():
    assert extract_records(_b({}), "u", "data") == []


def test_top_level_non_array_without_path_raises():
    with pytest.raises(SourceClientError, match="Expected a JSON array"):
        extract_records(_b({"not": "an array"}), "u")


def test_path_pointing_at_non_array_raises():
    with pytest.raises(SourceClientError, match="Expected a JSON array at"):
        extract_records(_b({"data": {"nope": 1}}), "u", "data")


def test_non_json_raises():
    with pytest.raises(SourceClientError, match="Non-JSON"):
        extract_records(b"not json", "u", "data")
