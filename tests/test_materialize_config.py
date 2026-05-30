"""The Materializer Config loader parses a kafka-typed sources.yaml entry into a
MaterializePlan and rejects malformed config. Credentials resolve from the
environment only.
"""

import pytest

from filedge.materialize.config import load_kafka_source
from filedge.materialize.errors import MaterializeConfigError

_VALID = """\
version: 1
sources:
  - name: orders-topic
    type: kafka
    brokers: broker1:9092,broker2:9092
    topic: orders
    consumer_group: filedge-orders
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    batch_size: 500
    batch_timeout_seconds: 10
    trigger: continuous
    format: json
    gzip: true
    credentials:
      sasl_password: env:KAFKA_PASSWORD
"""


def _write(tmp_path, text):
    path = tmp_path / "sources.yaml"
    path.write_text(text)
    return str(path)


def test_loads_a_valid_kafka_source(tmp_path):
    plan = load_kafka_source(_write(tmp_path, _VALID), "orders-topic")

    assert plan.source_name == "orders-topic"
    assert plan.source_type == "kafka"
    assert plan.brokers == ["broker1:9092", "broker2:9092"]
    assert plan.topic == "orders"
    assert plan.consumer_group == "filedge-orders"
    assert plan.batch_size == 500
    assert plan.batch_timeout_seconds == 10.0
    assert plan.trigger == "continuous"
    assert plan.gzip is True


def test_applies_defaults_when_optional_fields_absent(tmp_path):
    text = """\
version: 1
sources:
  - name: t
    type: kafka
    brokers: [b:9092]
    topic: t
    consumer_group: g
    staging_dir: ./s
    watched_directory: ./l
    state_dir: ./st
"""
    plan = load_kafka_source(_write(tmp_path, text), "t")
    assert plan.batch_size == 1000
    assert plan.batch_timeout_seconds == 30.0
    assert plan.trigger == "drain"      # Drain is the default Trigger Mode
    assert plan.decode_format == "json"
    assert plan.gzip is False
    assert plan.brokers == ["b:9092"]   # list form accepted as-is


def test_credentials_resolve_from_environment_only(tmp_path, monkeypatch):
    plan = load_kafka_source(_write(tmp_path, _VALID), "orders-topic")
    monkeypatch.setenv("KAFKA_PASSWORD", "s3cret")

    assert plan.credential("sasl_password") == "s3cret"
    assert plan.credential("nonexistent") is None
    assert "s3cret" not in _VALID  # never in the file


def test_unset_credential_env_raises(tmp_path, monkeypatch):
    plan = load_kafka_source(_write(tmp_path, _VALID), "orders-topic")
    monkeypatch.delenv("KAFKA_PASSWORD", raising=False)

    with pytest.raises(MaterializeConfigError, match="KAFKA_PASSWORD"):
        plan.credential("sasl_password")


def test_non_kafka_source_is_rejected(tmp_path):
    text = _VALID.replace("    type: kafka", "    type: github")
    with pytest.raises(MaterializeConfigError, match="not 'kafka'"):
        load_kafka_source(_write(tmp_path, text), "orders-topic")


def test_unknown_source_lists_known_names(tmp_path):
    with pytest.raises(MaterializeConfigError, match="orders-topic"):
        load_kafka_source(_write(tmp_path, _VALID), "missing")


def test_missing_required_field_rejected(tmp_path):
    text = _VALID.replace("    topic: orders\n", "")
    with pytest.raises(MaterializeConfigError, match="topic"):
        load_kafka_source(_write(tmp_path, text), "orders-topic")


def test_invalid_trigger_rejected(tmp_path):
    text = _VALID.replace("    trigger: continuous", "    trigger: streaming")
    with pytest.raises(MaterializeConfigError, match="trigger"):
        load_kafka_source(_write(tmp_path, text), "orders-topic")


def test_unsupported_version_rejected(tmp_path):
    text = _VALID.replace("version: 1", "version: 2")
    with pytest.raises(MaterializeConfigError, match="version"):
        load_kafka_source(_write(tmp_path, text), "orders-topic")


def test_missing_file_rejected(tmp_path):
    with pytest.raises(MaterializeConfigError, match="not found"):
        load_kafka_source(str(tmp_path / "nope.yaml"), "x")


def test_non_mapping_document_rejected(tmp_path):
    with pytest.raises(MaterializeConfigError, match="must be a mapping"):
        load_kafka_source(_write(tmp_path, "- a\n- b\n"), "x")


def test_empty_sources_list_rejected(tmp_path):
    with pytest.raises(MaterializeConfigError, match="non-empty"):
        load_kafka_source(_write(tmp_path, "version: 1\nsources: []\n"), "x")


def test_duplicate_source_name_rejected(tmp_path):
    text = _VALID + """\
  - name: orders-topic
    type: kafka
    brokers: [b:9092]
    topic: orders
    consumer_group: g2
    staging_dir: ./s
    watched_directory: ./l
    state_dir: ./st
"""
    with pytest.raises(MaterializeConfigError, match="Duplicate"):
        load_kafka_source(_write(tmp_path, text), "orders-topic")


def test_non_mapping_credentials_rejected(tmp_path):
    text = _VALID.replace(
        "    credentials:\n      sasl_password: env:KAFKA_PASSWORD\n",
        "    credentials: not-a-mapping\n",
    )
    with pytest.raises(MaterializeConfigError, match="credentials"):
        load_kafka_source(_write(tmp_path, text), "orders-topic")
