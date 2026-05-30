"""Adapt confluent-kafka to the consumer's ``QueueClient`` seam.

This is the only broker-specific code in the Reference Queue Materializer, and
the only place a Kafka client library is touched — gated behind the optional
``kafka`` extra so the core ingestion path and the Reference Fetcher take on no
Kafka dependency. The confluent-kafka module is injectable so the adapter's
mapping (assignment, watermark offsets, poll, commit, fail-fast) is testable
without a broker or the real library installed.
"""

from typing import Dict, List, Optional

from filedge.materialize.config import MaterializePlan
from filedge.materialize.consumer import Message, QueueClient, TopicPartition
from filedge.materialize.errors import MaterializeError


class BrokerUnreachable(MaterializeError):
    """Raised when the declared Kafka brokers cannot be reached at startup."""


def make_kafka_client(plan: MaterializePlan, *, confluent_kafka=None) -> QueueClient:
    """Build a `QueueClient` backed by confluent-kafka for this plan."""
    ck = confluent_kafka or _import_confluent()
    return _ConfluentQueueClient(plan, ck)


def _import_confluent():  # pragma: no cover - lazy import glue; tests inject a fake
    try:
        import confluent_kafka  # noqa: PLC0415
        return confluent_kafka
    except ImportError as e:
        raise MaterializeError(
            "The Reference Queue Materializer needs the 'kafka' extra: "
            "install with `pip install filedge[kafka]`."
        ) from e


class _ConfluentQueueClient:
    """Maps a confluent-kafka Consumer onto the consumer's QueueClient seam."""

    def __init__(self, plan: MaterializePlan, ck):
        self._ck = ck
        self._topic = plan.topic
        self._consumer = ck.Consumer(_consumer_config(plan))
        self._partitions = self._assign_all_partitions()

    def _assign_all_partitions(self) -> List[TopicPartition]:
        try:
            metadata = self._consumer.list_topics(self._topic, timeout=10)
        except Exception as e:  # confluent raises KafkaException
            raise BrokerUnreachable(
                f"Cannot reach Kafka brokers for topic {self._topic!r}: {e}"
            ) from e
        topic_meta = metadata.topics.get(self._topic)
        if topic_meta is None or getattr(topic_meta, "error", None) is not None:
            raise BrokerUnreachable(f"Topic {self._topic!r} not available on the brokers.")
        partition_ids = sorted(topic_meta.partitions.keys())
        tps = [self._ck.TopicPartition(self._topic, p) for p in partition_ids]
        self._consumer.assign(tps)
        return [(self._topic, p) for p in partition_ids]

    def assigned_partitions(self) -> List[TopicPartition]:
        return list(self._partitions)

    def end_offsets(self, partitions: List[TopicPartition]) -> Dict[TopicPartition, int]:
        offsets: Dict[TopicPartition, int] = {}
        for topic, partition in partitions:
            _low, high = self._consumer.get_watermark_offsets(
                self._ck.TopicPartition(topic, partition), timeout=10
            )
            offsets[(topic, partition)] = high
        return offsets

    def poll(self, timeout: float) -> List[Message]:
        raw = self._consumer.poll(timeout)
        if raw is None:
            return []
        if raw.error() is not None:
            raise MaterializeError(f"Kafka poll error: {raw.error()}")
        return [Message(raw.topic(), raw.partition(), raw.offset(), raw.value())]

    def commit(self, topic: str, partition: int, offset: int) -> None:
        self._consumer.commit(
            offsets=[self._ck.TopicPartition(topic, partition, offset)],
            asynchronous=False,
        )

    def close(self) -> None:
        self._consumer.close()


def _consumer_config(plan: MaterializePlan) -> dict:
    config = {
        "bootstrap.servers": ",".join(plan.brokers),
        "group.id": plan.consumer_group,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    }
    config.update(_security_config(plan))
    return config


def _security_config(plan: MaterializePlan) -> dict:
    """SASL/TLS settings: plain protocol/mechanism + env-resolved credentials."""
    out: dict = {}
    if plan.security_protocol:
        out["security.protocol"] = plan.security_protocol
    if plan.sasl_mechanism:
        out["sasl.mechanism"] = plan.sasl_mechanism
    username: Optional[str] = plan.credential("sasl_username")
    if username:
        out["sasl.username"] = username
    password: Optional[str] = plan.credential("sasl_password")
    if password:
        out["sasl.password"] = password
    return out
