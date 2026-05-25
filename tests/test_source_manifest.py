import json

from filedge.source_manifest import discover_and_parse


def test_parses_openlineage_shaped_sidecar(tmp_path):
    data_file = tmp_path / "stripe-charges.ndjson"
    data_file.write_text('{"id":1}\n')

    manifest = tmp_path / "stripe-charges.ndjson.manifest.json"
    manifest.write_text(json.dumps({
        "eventType": "COMPLETE",
        "eventTime": "2026-05-24T10:00:00Z",
        "producer": "https://github.com/dlt-hub/dlt",
        "run": {"runId": "dlt-run-abc-123"},
        "job": {"namespace": "api", "name": "stripe.charges"},
        "inputs": [],
        "outputs": [],
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.found is True
    assert result.metadata.source_type == "api"
    assert result.metadata.source_name == "stripe.charges"
    assert result.metadata.producer == "https://github.com/dlt-hub/dlt"
    assert result.metadata.external_run_id == "dlt-run-abc-123"


def test_returns_not_found_when_no_sidecar(tmp_path):
    data_file = tmp_path / "direct-drop.csv"
    data_file.write_text("name,value\nAlice,1\n")

    result = discover_and_parse(str(data_file), fs=None)

    assert result.found is False
    assert result.metadata is None
    assert result.error_category == "manifest_missing"
    assert result.manifest_path == str(data_file) + ".manifest.json"


def test_returns_malformed_json_for_invalid_json(tmp_path):
    data_file = tmp_path / "broken.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "broken.ndjson.manifest.json").write_text("{ not valid json")

    result = discover_and_parse(str(data_file), fs=None)

    assert result.found is True
    assert result.metadata is None
    assert result.error_category == "manifest_malformed_json"


def test_returns_unsupported_version_when_version_unknown(tmp_path):
    data_file = tmp_path / "future.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "future.ndjson.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r1", "facets": {"_filedgeManifest": {"manifest_version": "9999"}}},
        "job": {"namespace": "api", "name": "x"},
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.error_category == "manifest_unsupported_version"


def test_returns_missing_required_field_when_namespace_or_name_missing(tmp_path):
    data_file = tmp_path / "no-job.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "no-job.ndjson.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r1"},
        "job": {"namespace": "api"},  # missing job.name
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.error_category == "manifest_missing_required_field"


def test_returns_invalid_source_range_when_not_an_object(tmp_path):
    data_file = tmp_path / "bad-range.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "bad-range.ndjson.manifest.json").write_text(json.dumps({
        "producer": "x",
        "run": {"runId": "r1"},
        "job": {"namespace": "api", "name": "x"},
        "inputs": [{"name": "src", "facets": {"_sourceRange": "not-an-object"}}],
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.error_category == "manifest_invalid_source_range"
