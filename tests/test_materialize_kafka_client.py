"""The confluent-kafka adapter maps the broker onto the QueueClient seam.
Exercised with a fake confluent module injected — no real library or broker.
"""

from types import SimpleNamespace

import pytest

from filedge.materialize.config import MaterializePlan
from filedge.materialize.consumer import Message
from filedge.materialize.kafka_client import BrokerUnreachable, make_kafka_client


# --- a minimal fake confluent-kafka ---

class _FakeTP:
    def __init__(self, topic, partition, offset=None):
        self.topic = topic
        self.partition = partition
        self.offset = offset


class _FakeMsg:
    def __init__(self, topic, partition, offset, value, error=None):
        self._t, self._p, self._o, self._v, self._e = topic, partition, offset, value, error

    def topic(self):
        return self._t

    def partition(self):
        return self._p

    def offset(self):
        return self._o

    def value(self):
        return self._v

    def error(self):
        return self._e


def _metadata(topic, partition_ids, *, error=None):
    topic_meta = SimpleNamespace(
        partitions={p: object() for p in partition_ids}, error=error
    )
    return SimpleNamespace(topics={topic: topic_meta})


class _FakeConsumer:
    def __init__(self, config, *, list_topics_raises=False, metadata=None,
                 watermarks=(0, 0), polls=None):
        self.config = config
        self.assigned = None
        self.committed = []
        self.closed = False
        self._list_topics_raises = list_topics_raises
        self._metadata = metadata
        self._watermarks = watermarks
        self._polls = list(polls or [])

    def list_topics(self, topic, timeout):
        if self._list_topics_raises:
            raise RuntimeError("no brokers")
        return self._metadata

    def assign(self, tps):
        self.assigned = tps

    def get_watermark_offsets(self, tp, timeout):
        return self._watermarks

    def poll(self, timeout):
        return self._polls.pop(0) if self._polls else None

    def commit(self, offsets, asynchronous):
        self.committed.append((offsets[0].topic, offsets[0].partition, offsets[0].offset))

    def close(self):
        self.closed = True


def _fake_ck(consumer):
    return SimpleNamespace(Consumer=lambda config: consumer, TopicPartition=_FakeTP)


def _plan(**overrides):
    base = dict(
        source_name="s", source_type="kafka", brokers=["b1:9092", "b2:9092"], topic="orders",
        consumer_group="g", staging_dir="s", watched_directory="w", state_dir="st",
    )
    base.update(overrides)
    return MaterializePlan(**base)


def test_assigns_all_topic_partitions_at_startup():
    consumer = _FakeConsumer({}, metadata=_metadata("orders", [0, 1, 2]))
    client = make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))

    assert client.assigned_partitions() == [("orders", 0), ("orders", 1), ("orders", 2)]
    assert [(tp.topic, tp.partition) for tp in consumer.assigned] == [
        ("orders", 0), ("orders", 1), ("orders", 2)
    ]


def test_end_offsets_reads_high_watermarks():
    consumer = _FakeConsumer({}, metadata=_metadata("orders", [0]), watermarks=(0, 42))
    client = make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))

    assert client.end_offsets([("orders", 0)]) == {("orders", 0): 42}


def test_poll_maps_a_message_and_empty_poll_to_list():
    msg = _FakeMsg("orders", 0, 7, b'{"id": 1}')
    consumer = _FakeConsumer({}, metadata=_metadata("orders", [0]), polls=[msg, None])
    client = make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))

    assert client.poll(1.0) == [Message("orders", 0, 7, b'{"id": 1}')]
    assert client.poll(1.0) == []  # None -> empty list


def test_poll_error_raises():
    from filedge.materialize.errors import MaterializeError

    err_msg = _FakeMsg("orders", 0, 0, None, error="broker down")
    consumer = _FakeConsumer({}, metadata=_metadata("orders", [0]), polls=[err_msg])
    client = make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))

    with pytest.raises(MaterializeError, match="poll error"):
        client.poll(1.0)


def test_commit_passes_topic_partition_offset():
    consumer = _FakeConsumer({}, metadata=_metadata("orders", [0]))
    client = make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))

    client.commit("orders", 0, 10)
    client.close()

    assert consumer.committed == [("orders", 0, 10)]
    assert consumer.closed is True


def test_unreachable_brokers_fail_fast():
    consumer = _FakeConsumer({}, list_topics_raises=True)
    with pytest.raises(BrokerUnreachable, match="Cannot reach Kafka brokers"):
        make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))


def test_missing_topic_fails_fast():
    consumer = _FakeConsumer({}, metadata=SimpleNamespace(topics={}))
    with pytest.raises(BrokerUnreachable, match="not available"):
        make_kafka_client(_plan(), confluent_kafka=_fake_ck(consumer))


def test_security_config_resolves_credentials_from_env(monkeypatch):
    monkeypatch.setenv("KP", "pw")
    monkeypatch.setenv("KU", "user")
    plan = _plan(
        security_protocol="SASL_SSL",
        sasl_mechanism="PLAIN",
        credentials={"sasl_username": "env:KU", "sasl_password": "env:KP"},
    )
    captured = {}

    def capture_consumer(config):
        captured.update(config)
        return _FakeConsumer(config, metadata=_metadata("orders", [0]))

    make_kafka_client(plan, confluent_kafka=SimpleNamespace(
        Consumer=capture_consumer, TopicPartition=_FakeTP))

    assert captured["security.protocol"] == "SASL_SSL"
    assert captured["sasl.mechanism"] == "PLAIN"
    assert captured["sasl.username"] == "user"
    assert captured["sasl.password"] == "pw"
    assert captured["enable.auto.commit"] is False
