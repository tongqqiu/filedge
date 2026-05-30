"""Wire the Reference Queue Materializer together for one Drain cycle.

The ordering is the contract and is fixed here, per Micro-batch:

    decode records -> write complete staged NDJSON (offset-range-named) ->
    emit Source Manifest sidecar (offset range) -> [Fetch Lock] promote
    sidecar+data into the Watched Directory -> commit the broker offset

The broker offset is committed **only after** a successful promotion, so a crash
anywhere earlier re-consumes the same offset range rather than losing it
(ADR-0007). A decode failure fails that Micro-batch before its offset is
committed. An empty Drain (no new records past the committed offset) is a clean
no-op: nothing staged, promoted, or committed.

Drain only here; Continuous Trigger Mode is added in a later slice.
"""

import signal
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from filedge.companion.manifest import emit_manifest
from filedge.companion.promotion import FetchLock, promote
from filedge.companion.staging import write_staged_ndjson
from filedge.materialize.config import MaterializePlan, load_kafka_source
from filedge.materialize.consumer import MicroBatch, QueueConsumer
from filedge.materialize.decoder import get_decoder


@dataclass(frozen=True)
class MaterializeOutcome:
    source_name: str
    batch_count: int
    record_count: int
    promoted: List[str] = field(default_factory=list)
    dry_run: bool = False
    skipped: bool = False
    topic: Optional[str] = None
    watched_directory: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_materialize(
    config_path: str,
    source_name: str,
    *,
    dry_run: bool = False,
    consumer: Optional[QueueConsumer] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> MaterializeOutcome:
    """Materialize one Kafka Queue Source (Drain or Continuous), or report a dry-run.

    ``should_stop`` lets a test drive Continuous mode deterministically; in
    production it defaults to a SIGTERM-set flag.
    """
    plan = load_kafka_source(config_path, source_name)

    if dry_run:
        return MaterializeOutcome(
            source_name=source_name, batch_count=0, record_count=0, dry_run=True,
            topic=plan.topic, watched_directory=plan.watched_directory,
        )

    decoder = get_decoder(plan.decode_format)
    consumer = consumer or _build_consumer(plan)
    promoted: List[str] = []
    record_count = 0
    try:
        for batch in _batches(consumer, plan.trigger, should_stop):
            data_path, n = _materialize_batch(plan, decoder, batch)
            # Commit only now — after the File is durably in the Watched Directory.
            consumer.commit_batch(batch)
            promoted.append(data_path)
            record_count += n
    finally:
        consumer.close()

    return MaterializeOutcome(
        source_name=source_name,
        batch_count=len(promoted),
        record_count=record_count,
        promoted=promoted,
        skipped=not promoted,
        topic=plan.topic,
        watched_directory=plan.watched_directory,
    )


def _batches(consumer, trigger, should_stop):
    """Pick the Micro-batch source for the Trigger Mode."""
    if trigger == "continuous":
        return consumer.consume_continuous(should_stop or _install_sigterm_stop())
    return consumer.drain()


def _install_sigterm_stop() -> Callable[[], bool]:  # pragma: no cover - signal wiring
    """A stop predicate flipped by SIGTERM, for Continuous mode in production."""
    flag = {"stop": False}

    def _handler(signum, frame):
        flag["stop"] = True

    signal.signal(signal.SIGTERM, _handler)
    return lambda: flag["stop"]


def _materialize_batch(plan: MaterializePlan, decoder, batch: MicroBatch):
    started_at = _utc_now_iso()
    records = [decoder.decode(payload) for payload in batch.messages]
    finished_at = _utc_now_iso()

    data_path = write_staged_ndjson(
        records,
        plan.staging_dir,
        plan.source_name,
        from_cursor=f"{batch.topic}.{batch.partition}.{batch.start_offset}",
        to_cursor=str(batch.end_offset),
        timestamp=finished_at,
        gzip_enabled=plan.gzip,
    )
    sidecar_path = emit_manifest(
        data_path,
        source_type=plan.source_type,
        source_name=plan.source_name,
        producer=plan.producer,
        run_id=uuid.uuid4().hex,
        started_at=started_at,
        finished_at=finished_at,
        record_count=len(records),
        source_range={
            "topic": batch.topic,
            "partition": batch.partition,
            "start_offset": batch.start_offset,
            "end_offset": batch.end_offset,
        },
    )
    with FetchLock(plan.state_dir, plan.source_name):
        promotion = promote(data_path, sidecar_path, plan.watched_directory)
    return promotion.data_path, len(records)


def _build_consumer(plan: MaterializePlan) -> QueueConsumer:  # pragma: no cover - real broker wiring; tests inject a fake consumer
    from filedge.materialize.kafka_client import make_kafka_client

    return QueueConsumer(make_kafka_client(plan), plan)
