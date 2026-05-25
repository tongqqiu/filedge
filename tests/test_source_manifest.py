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
