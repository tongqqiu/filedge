"""The Drain orchestrator's ordering is the contract: a complete File + sidecar
land in the Watched Directory, the broker offset is committed only after
promotion, an empty Drain is a no-op, a decode failure fails the batch before
commit, and a promotion failure leaves the offset un-committed. One end-to-end
test ingests the promoted File with `filedge run`.
"""

import json
import os

import pytest
from click.testing import CliRunner

from filedge.cli import cli
from filedge.materialize.consumer import MicroBatch
from filedge.materialize.errors import DecodeError
from filedge.materialize.orchestrator import run_materialize
from filedge.source_manifest import discover_and_parse


class FakeConsumer:
    """Yields pre-built Micro-batches; records the offsets it was asked to commit."""

    def __init__(self, batches):
        self._batches = batches
        self.committed = []
        self.closed = False
        self.continuous_stop = None

    def drain(self):
        return iter(self._batches)

    def consume_continuous(self, should_stop):
        self.continuous_stop = should_stop
        return iter(self._batches)

    def commit_batch(self, batch):
        self.committed.append((batch.topic, batch.partition, batch.end_offset + 1))

    def close(self):
        self.closed = True


def _sources_yaml(tmp_path):
    staging, landing, state = tmp_path / "staging", tmp_path / "landing", tmp_path / "state"
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(
        "version: 1\n"
        "sources:\n"
        "  - name: orders\n"
        "    type: kafka\n"
        "    brokers: [b:9092]\n"
        "    topic: orders\n"
        "    consumer_group: g\n"
        f"    staging_dir: {staging}\n"
        f"    watched_directory: {landing}\n"
        f"    state_dir: {state}\n"
    )
    return str(cfg), staging, landing, state


def _batch(partition, start, msgs):
    payloads = [json.dumps(m).encode() for m in msgs]
    return MicroBatch("orders", partition, start, start + len(msgs) - 1, payloads)


def test_drain_promotes_file_and_sidecar_then_commits_offset(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    consumer = FakeConsumer([_batch(0, 0, [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])])

    outcome = run_materialize(cfg, "orders", consumer=consumer)

    assert outcome.batch_count == 1
    assert outcome.record_count == 2
    landed = os.listdir(landing)
    assert sum(n.endswith(".ndjson") for n in landed) == 1
    assert sum(n.endswith(".manifest.json") for n in landed) == 1
    assert os.listdir(staging) == []                 # moved, not copied
    assert consumer.committed == [("orders", 0, 2)]  # last offset (1) + 1
    assert consumer.closed is True
    # The sidecar round-trips and carries the offset range.
    md = discover_and_parse(outcome.promoted[0]).metadata
    assert md.source_name == "orders"
    assert md.source_range == {
        "topic": "orders", "partition": 0, "start_offset": 0, "end_offset": 1
    }


def test_continuous_trigger_routes_through_consume_continuous(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    # Flip the source's trigger to continuous.
    cfg_text = open(cfg).read().replace(
        "    consumer_group: g\n", "    consumer_group: g\n    trigger: continuous\n"
    )
    open(cfg, "w").write(cfg_text)
    consumer = FakeConsumer([_batch(0, 0, [{"id": 1}])])

    outcome = run_materialize(cfg, "orders", consumer=consumer, should_stop=lambda: True)

    assert outcome.batch_count == 1
    assert consumer.continuous_stop is not None  # continuous path was taken
    assert consumer.committed == [("orders", 0, 1)]


def test_empty_drain_is_a_noop(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    consumer = FakeConsumer([])

    outcome = run_materialize(cfg, "orders", consumer=consumer)

    assert outcome.skipped is True
    assert outcome.batch_count == 0
    assert consumer.committed == []
    assert not landing.exists() or os.listdir(landing) == []


def test_promotion_failure_leaves_offset_uncommitted(tmp_path, monkeypatch):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    consumer = FakeConsumer([_batch(0, 0, [{"id": 1}])])

    import filedge.companion.published_file as pub
    monkeypatch.setattr(pub, "promote", lambda *a, **k: (_ for _ in ()).throw(OSError("landing down")))

    with pytest.raises(OSError):
        run_materialize(cfg, "orders", consumer=consumer)

    assert consumer.committed == []  # offset NOT advanced; range re-consumed next run


def test_decode_failure_fails_the_batch_before_commit(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    bad = MicroBatch("orders", 0, 0, 0, [b"not json"])
    consumer = FakeConsumer([bad])

    with pytest.raises(DecodeError):
        run_materialize(cfg, "orders", consumer=consumer)

    assert consumer.committed == []
    assert not landing.exists() or os.listdir(landing) == []


def test_dry_run_reports_without_consuming(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)

    outcome = run_materialize(cfg, "orders", dry_run=True)

    assert outcome.dry_run is True
    assert outcome.topic == "orders"
    assert not landing.exists() or os.listdir(landing) == []


def test_cli_reports_materialized_counts(tmp_path, monkeypatch):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    from filedge.materialize.cli import materialize
    from filedge.materialize.orchestrator import MaterializeOutcome

    monkeypatch.setattr(
        "filedge.materialize.cli.run_materialize",
        lambda *a, **k: MaterializeOutcome(
            source_name="orders", batch_count=2, record_count=5,
            promoted=["/l/a.ndjson", "/l/b.ndjson"], topic="orders",
            watched_directory="/l",
        ),
    )
    result = CliRunner().invoke(materialize, ["--config", cfg, "--source", "orders"])
    assert result.exit_code == 0
    assert "materialized 2 Micro-batches (5 records)" in result.output


def test_cli_dry_run_reports_target(tmp_path, monkeypatch):
    cfg, *_ = _sources_yaml(tmp_path)
    from filedge.materialize.cli import materialize
    from filedge.materialize.orchestrator import MaterializeOutcome

    monkeypatch.setattr(
        "filedge.materialize.cli.run_materialize",
        lambda *a, **k: MaterializeOutcome(
            source_name="orders", batch_count=0, record_count=0, dry_run=True,
            topic="orders", watched_directory="/l",
        ),
    )
    result = CliRunner().invoke(materialize, ["--config", cfg, "--source", "orders", "--dry-run"])
    assert result.exit_code == 0
    assert "[dry-run]" in result.output
    assert "orders" in result.output


def test_cli_skipped_reports_noop(tmp_path, monkeypatch):
    cfg, *_ = _sources_yaml(tmp_path)
    from filedge.materialize.cli import materialize
    from filedge.materialize.orchestrator import MaterializeOutcome

    monkeypatch.setattr(
        "filedge.materialize.cli.run_materialize",
        lambda *a, **k: MaterializeOutcome(
            source_name="orders", batch_count=0, record_count=0, skipped=True, topic="orders",
        ),
    )
    result = CliRunner().invoke(materialize, ["--config", cfg, "--source", "orders"])
    assert result.exit_code == 0
    assert "no new records" in result.output


def test_cli_error_exits_nonzero(tmp_path):
    cfg, *_ = _sources_yaml(tmp_path)
    from filedge.materialize.cli import materialize

    result = CliRunner().invoke(materialize, ["--config", cfg, "--source", "missing"])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_promoted_file_is_ingestable_by_filedge_run(tmp_path):
    cfg, staging, landing, state = _sources_yaml(tmp_path)
    consumer = FakeConsumer([_batch(0, 0, [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])])
    outcome = run_materialize(cfg, "orders", consumer=consumer)

    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        "format: ndjson\n"
        "dest_table: orders\n"
        "connector:\n"
        f"  type: sqlite\n"
        f"  url: sqlite:///{tmp_path}/dest.db\n"
        "columns:\n"
        "  - source: id\n"
        "    dest: id\n"
        "    type: integer\n"
        "    required: true\n"
        "  - source: v\n"
        "    dest: v\n"
        "    type: string\n"
        "    required: true\n"
    )
    result = CliRunner().invoke(cli, [
        "run", "--dir", str(landing), "--config", str(config_file),
        "--audit-db-url", f"sqlite:///{tmp_path}/audit.db", "--no-progress",
    ])
    assert result.exit_code == 0, result.output
    assert "Committed: 1" in result.output

    lineage = CliRunner().invoke(cli, [
        "lineage", os.path.basename(outcome.promoted[0]),
        "--audit-db-url", f"sqlite:///{tmp_path}/audit.db",
    ])
    assert lineage.exit_code == 0, lineage.output
    assert "orders" in lineage.output
