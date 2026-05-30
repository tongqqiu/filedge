"""Consume a Kafka Queue Source into per-partition Micro-batches.

This module owns the consumer concerns ADR-0007 keeps *external* to `filedge
run`: partition assignment, the count-or-time batch boundary, and the Drain
high-water-mark snapshot. The broker client is injected behind a small
``QueueClient`` interface, so all of that logic is testable against a fake
in-memory client with no broker (the reference Kafka adapter lives in
``kafka_client``).

Two rules from issue #18 / ADR-0007 are structural here:

- **One Micro-batch per partition per cut** — each partition's work is an
  independently retryable File; partitions are never mixed into one batch.
- **Drain snapshots the high-water mark at startup** — messages that arrive
  after the snapshot (offset >= the snapshot end) are excluded from this cycle,
  and a partition stops once it reaches its snapshot end.

The consumer only *produces* Micro-batches and commits offsets when asked; the
orchestrator decides *when* to commit (only after a File is promoted).
"""

import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Protocol, Tuple, runtime_checkable

from filedge.materialize.config import MaterializePlan

TopicPartition = Tuple[str, int]


@dataclass(frozen=True)
class Message:
    """One consumed record: its position and raw (still-encoded) value."""

    topic: str
    partition: int
    offset: int
    value: bytes


@dataclass(frozen=True)
class MicroBatch:
    """A complete, independently-retryable slice of one partition.

    ``messages`` are the raw payload bytes in offset order; decoding into rows
    is the orchestrator's job (via the Decoder). ``end_offset`` is the offset of
    the last message — the broker offset to commit is ``end_offset + 1``.
    """

    topic: str
    partition: int
    start_offset: int
    end_offset: int
    messages: List[bytes]


@runtime_checkable
class QueueClient(Protocol):
    """The minimal broker seam the consumer drives (real impl in kafka_client)."""

    def assigned_partitions(self) -> List[TopicPartition]: ...

    def end_offsets(self, partitions: List[TopicPartition]) -> Dict[TopicPartition, int]: ...

    def poll(self, timeout: float) -> List[Message]: ...

    def commit(self, topic: str, partition: int, offset: int) -> None: ...

    def close(self) -> None: ...


class QueueConsumer:
    """Cut per-partition Micro-batches on a count-or-time boundary."""

    def __init__(
        self,
        client: QueueClient,
        plan: MaterializePlan,
        *,
        poll_timeout: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._batch_size = plan.batch_size
        self._batch_timeout = plan.batch_timeout_seconds
        self._poll_timeout = poll_timeout
        self._monotonic = monotonic

    def commit_batch(self, batch: MicroBatch) -> None:
        """Commit the broker offset past this Micro-batch (last offset + 1)."""
        self._client.commit(batch.topic, batch.partition, batch.end_offset + 1)

    def drain(self) -> Iterator[MicroBatch]:
        """Yield Micro-batches up to the per-partition high-water mark, then stop.

        Snapshots each assigned partition's end offset at startup. A partition is
        ``exhausted`` once consumption reaches that end; the cycle finishes when
        every partition is exhausted and every buffer has been flushed.
        """
        partitions = self._client.assigned_partitions()
        hwm = self._client.end_offsets(partitions)
        buffers: Dict[TopicPartition, List[Message]] = {}
        opened_at: Dict[TopicPartition, float] = {}
        exhausted = {tp for tp in partitions if hwm.get(tp, 0) <= 0}

        def open_buffer(tp: TopicPartition) -> None:
            if tp not in buffers:
                buffers[tp] = []
                opened_at[tp] = self._monotonic()

        def cut(tp: TopicPartition) -> MicroBatch:
            buf = buffers.pop(tp)
            opened_at.pop(tp, None)
            return MicroBatch(
                topic=tp[0], partition=tp[1],
                start_offset=buf[0].offset, end_offset=buf[-1].offset,
                messages=[m.value for m in buf],
            )

        while True:
            ready: List[TopicPartition] = [
                tp for tp, buf in buffers.items()
                if buf and (tp in exhausted or self._timed_out(opened_at[tp]))
            ]
            for tp in ready:
                yield cut(tp)

            if all(tp in exhausted for tp in partitions) and not buffers:
                return

            messages = self._client.poll(self._poll_timeout)
            if not messages:
                # Drain is bounded: an empty poll with everything consumed means
                # we are done. Flush whatever remains and stop (no infinite wait).
                for tp in list(buffers):
                    if buffers[tp]:
                        yield cut(tp)
                return

            for message in messages:
                tp = (message.topic, message.partition)
                if tp in exhausted:
                    continue
                if message.offset >= hwm[tp]:
                    # Arrived after the startup snapshot — excluded from this cycle.
                    exhausted.add(tp)
                    continue
                open_buffer(tp)
                buffers[tp].append(message)
                if message.offset + 1 >= hwm[tp]:
                    exhausted.add(tp)
                if len(buffers[tp]) >= self._batch_size:
                    yield cut(tp)

    def _timed_out(self, opened: float) -> bool:
        return (self._monotonic() - opened) >= self._batch_timeout
