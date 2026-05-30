"""Drain + count/time batch boundary, against a fake in-memory client (no broker).
Asserts the Micro-batches yielded and the offsets committed — external behavior.
"""

from filedge.materialize.config import MaterializePlan
from filedge.materialize.consumer import Message, MicroBatch, QueueConsumer


class FakeQueueClient:
    """In-memory QueueClient: hands out pre-seeded poll batches, records commits."""

    def __init__(self, partitions, polls, end_offsets):
        self._partitions = partitions
        self._polls = list(polls)
        self._end = dict(end_offsets)
        self.committed = []
        self.closed = False

    def assigned_partitions(self):
        return list(self._partitions)

    def end_offsets(self, partitions):
        return dict(self._end)

    def poll(self, timeout):
        return self._polls.pop(0) if self._polls else []

    def commit(self, topic, partition, offset):
        self.committed.append((topic, partition, offset))

    def close(self):
        self.closed = True


def _plan(batch_size=100, batch_timeout=30.0):
    return MaterializePlan(
        source_name="s", source_type="kafka", brokers=["b:9092"], topic="t",
        consumer_group="g", staging_dir="s", watched_directory="w", state_dir="st",
        batch_size=batch_size, batch_timeout_seconds=batch_timeout,
    )


def _m(partition, offset, value):
    return Message("t", partition, offset, value.encode())


def _frozen() -> float:  # no time-based cuts unless a test wants them
    return 0.0


_FROZEN = _frozen


def test_drain_stops_at_high_water_mark_and_excludes_later_messages():
    client = FakeQueueClient(
        partitions=[("t", 0)],
        polls=[[_m(0, 0, "a"), _m(0, 1, "b"), _m(0, 2, "arrived-after-snapshot")]],
        end_offsets={("t", 0): 2},  # snapshot: offsets 0,1 valid; 2 is past the mark
    )
    consumer = QueueConsumer(client, _plan(), monotonic=_FROZEN)

    batches = list(consumer.drain())

    assert len(batches) == 1
    assert batches[0] == MicroBatch("t", 0, 0, 1, [b"a", b"b"])


def test_count_boundary_cuts_every_batch_size_records():
    client = FakeQueueClient(
        partitions=[("t", 0)],
        polls=[[_m(0, 0, "a"), _m(0, 1, "b"), _m(0, 2, "c"), _m(0, 3, "d")]],
        end_offsets={("t", 0): 4},
    )
    consumer = QueueConsumer(client, _plan(batch_size=2), monotonic=_FROZEN)

    batches = list(consumer.drain())

    assert [(b.start_offset, b.end_offset) for b in batches] == [(0, 1), (2, 3)]
    assert [b.messages for b in batches] == [[b"a", b"b"], [b"c", b"d"]]


def test_time_boundary_cuts_before_the_count_is_reached():
    import itertools

    clock = itertools.count(0, 100)  # every monotonic() call jumps 100s
    client = FakeQueueClient(
        partitions=[("t", 0)],
        polls=[[_m(0, 0, "a")], []],   # one record, then a quiet poll
        end_offsets={("t", 0): 10},    # not exhausted by the single record
    )
    consumer = QueueConsumer(
        client, _plan(batch_size=100, batch_timeout=5.0), monotonic=lambda: next(clock)
    )

    batches = list(consumer.drain())

    assert len(batches) == 1
    assert batches[0].messages == [b"a"]  # cut by the time window, not the count


def test_one_microbatch_per_partition_per_cut():
    client = FakeQueueClient(
        partitions=[("t", 0), ("t", 1)],
        polls=[[_m(0, 0, "a0"), _m(1, 0, "b0"), _m(0, 1, "a1"), _m(1, 1, "b1")]],
        end_offsets={("t", 0): 2, ("t", 1): 2},
    )
    consumer = QueueConsumer(client, _plan(), monotonic=_FROZEN)

    batches = list(consumer.drain())

    by_partition = {b.partition: b for b in batches}
    assert set(by_partition) == {0, 1}
    assert by_partition[0].messages == [b"a0", b"a1"]
    assert by_partition[1].messages == [b"b0", b"b1"]


def test_empty_topic_is_a_clean_noop():
    client = FakeQueueClient(
        partitions=[("t", 0)], polls=[], end_offsets={("t", 0): 0},
    )
    consumer = QueueConsumer(client, _plan(), monotonic=_FROZEN)

    assert list(consumer.drain()) == []


def test_empty_poll_flushes_remaining_buffer_and_stops():
    # Partition not yet exhausted (hwm high), no time cut — an empty poll must
    # still flush the buffered remainder rather than wait forever.
    client = FakeQueueClient(
        partitions=[("t", 0)],
        polls=[[_m(0, 0, "a"), _m(0, 1, "b")], []],
        end_offsets={("t", 0): 100},
    )
    consumer = QueueConsumer(client, _plan(batch_size=100), monotonic=_FROZEN)

    batches = list(consumer.drain())

    assert len(batches) == 1
    assert batches[0].messages == [b"a", b"b"]


def test_message_at_or_past_snapshot_is_excluded_when_partition_not_yet_exhausted():
    # The only delivered message sits at the snapshot end (offsets below it were
    # already consumed) — it must be excluded, yielding nothing.
    client = FakeQueueClient(
        partitions=[("t", 0)],
        polls=[[_m(0, 5, "after-snapshot")]],
        end_offsets={("t", 0): 5},
    )
    consumer = QueueConsumer(client, _plan(), monotonic=_FROZEN)

    assert list(consumer.drain()) == []


def test_commit_batch_commits_one_past_the_last_offset():
    client = FakeQueueClient(partitions=[("t", 0)], polls=[], end_offsets={("t", 0): 0})
    consumer = QueueConsumer(client, _plan(), monotonic=_FROZEN)

    consumer.commit_batch(MicroBatch("t", 0, 5, 9, [b"x"]))

    assert client.committed == [("t", 0, 10)]


def test_close_releases_the_client():
    client = FakeQueueClient(partitions=[("t", 0)], polls=[], end_offsets={("t", 0): 0})
    consumer = QueueConsumer(client, _plan(), monotonic=_FROZEN)

    consumer.close()

    assert client.closed is True
