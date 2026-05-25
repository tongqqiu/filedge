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


def test_parses_started_finished_record_count_and_version(tmp_path):
    data_file = tmp_path / "kafka.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "kafka.ndjson.manifest.json").write_text(json.dumps({
        "eventType": "COMPLETE",
        "eventTime": "2026-05-24T10:30:00Z",
        "producer": "https://github.com/apache/kafka-connect",
        "run": {
            "runId": "kc-run-1",
            "facets": {
                "_filedgeManifest": {
                    "manifest_version": "1",
                    "started_at": "2026-05-24T10:00:00Z",
                    "record_count": 1500,
                },
            },
        },
        "job": {"namespace": "queue", "name": "kafka.orders"},
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.metadata.manifest_version == "1"
    assert result.metadata.started_at == "2026-05-24T10:00:00Z"
    assert result.metadata.finished_at == "2026-05-24T10:30:00Z"
    assert result.metadata.record_count == 1500


def test_parses_kafka_offset_range(tmp_path):
    data_file = tmp_path / "kafka.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "kafka.ndjson.manifest.json").write_text(json.dumps({
        "producer": "https://github.com/apache/kafka-connect",
        "run": {"runId": "kc-run-1"},
        "job": {"namespace": "queue", "name": "kafka.orders"},
        "inputs": [{
            "name": "kafka://broker/orders",
            "facets": {"_sourceRange": {
                "topic": "orders", "partition": 3,
                "start_offset": 1000, "end_offset": 2000,
            }},
        }],
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.metadata.source_range == {
        "topic": "orders", "partition": 3,
        "start_offset": 1000, "end_offset": 2000,
    }


def test_parses_api_cursor_range(tmp_path):
    data_file = tmp_path / "stripe.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "stripe.ndjson.manifest.json").write_text(json.dumps({
        "producer": "https://github.com/dlt-hub/dlt",
        "run": {"runId": "dlt-run-1"},
        "job": {"namespace": "api", "name": "stripe.charges"},
        "inputs": [{
            "name": "https://api.stripe.com/v1/charges",
            "facets": {"_sourceRange": {
                "cursor_start": "ch_aaa", "cursor_end": "ch_zzz",
                "endpoint": "/v1/charges",
            }},
        }],
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.metadata.source_range == {
        "cursor_start": "ch_aaa", "cursor_end": "ch_zzz",
        "endpoint": "/v1/charges",
    }


def test_parses_sftp_partner_remote_path(tmp_path):
    data_file = tmp_path / "sftp.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "sftp.ndjson.manifest.json").write_text(json.dumps({
        "producer": "https://rclone.org",
        "run": {"runId": "rclone-run-1"},
        "job": {"namespace": "sftp", "name": "acme-partner"},
        "inputs": [{
            "name": "sftp://acme/inbox/file.csv",
            "facets": {"_sourceRange": {
                "partner": "acme", "remote_path": "/inbox/file.csv",
            }},
        }],
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.metadata.source_range == {"partner": "acme", "remote_path": "/inbox/file.csv"}


def test_parses_vendor_export_job_id(tmp_path):
    data_file = tmp_path / "vendor.ndjson"
    data_file.write_text("{}\n")
    (tmp_path / "vendor.ndjson.manifest.json").write_text(json.dumps({
        "producer": "https://salesforce.com",
        "run": {"runId": "sf-run-1"},
        "job": {"namespace": "vendor_export", "name": "salesforce.account"},
        "inputs": [{
            "name": "salesforce://Account",
            "facets": {"_sourceRange": {"export_job_id": "750xx0000004C92"}},
        }],
    }))

    result = discover_and_parse(str(data_file), fs=None)

    assert result.metadata.source_range == {"export_job_id": "750xx0000004C92"}


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
