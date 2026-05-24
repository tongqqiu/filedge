"""Tests for OTel metrics support (filedge.progress.MetricsProgressReporter).

Skipped when the `otel` extra is not installed.
"""
import pytest

pytest.importorskip("opentelemetry")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402


@pytest.fixture
def metric_reader():
    """Return (reader, meter) wired together via a local MeterProvider."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("filedge.test")
    yield reader, meter
    provider.shutdown()


def _counter_total(reader, metric_name):
    """Sum all data points for a counter (or counter-like) metric."""
    data = reader.get_metrics_data()
    if data is None:
        return 0
    total = 0
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != metric_name:
                    continue
                for point in metric.data.data_points:
                    total += point.value
    return total


def _data_points(reader, metric_name):
    """Return all data points (with .attributes) for a metric."""
    data = reader.get_metrics_data()
    points = []
    if data is None:
        return points
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == metric_name:
                    points.extend(metric.data.data_points)
    return points


def _minimal_pipeline(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(
        f"format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n  type: sqlite\n  url: sqlite:///{tmp_path}/dest.db\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: value\n    dest: value\n    type: string\n    required: true\n"
    )
    return str(watched), str(config_file), f"sqlite:///{tmp_path}/audit.db"


def test_should_enable_metrics_precedence():
    from filedge.metrics import should_enable_metrics

    assert should_enable_metrics(cli_flag=None, env_value=None) is False
    assert should_enable_metrics(cli_flag=None, env_value="") is False
    assert should_enable_metrics(cli_flag=None, env_value="true") is True
    assert should_enable_metrics(cli_flag=None, env_value="false") is False
    assert should_enable_metrics(cli_flag=True, env_value=None) is True
    assert should_enable_metrics(cli_flag=True, env_value="false") is True
    assert should_enable_metrics(cli_flag=False, env_value="true") is False


def test_cli_run_with_otel_metrics_flag_completes_without_crash(tmp_path):
    import json
    from click.testing import CliRunner
    from filedge.cli import cli

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        f"format: csv\ndest_table: items\nretry_cap: 3\nbatch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n  type: sqlite\n  url: sqlite:///{tmp_path}/dest.db\n"
        f"columns:\n"
        f"  - source: name\n    dest: name\n    type: string\n    required: true\n"
        f"  - source: value\n    dest: value\n    type: string\n    required: true\n"
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "run",
        "--dir", str(watched),
        "--config", str(config_path),
        "--audit-db-url", f"sqlite:///{tmp_path}/audit.db",
        "--no-progress", "--json",
        "--otel-metrics",
    ], env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"})

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["committed"] == 1


def test_metrics_reporter_increments_committed_counter_on_successful_file(tmp_path, metric_reader):
    from filedge.pipeline import run_pipeline
    from filedge.progress import MetricsProgressReporter

    reader, meter = metric_reader
    watched, config, audit = _minimal_pipeline(tmp_path)

    reporter = MetricsProgressReporter(run_id="run-m-1", meter=meter)
    run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-m-1")

    assert _counter_total(reader, "filedge.files.committed") == 1


def test_stale_processing_gauge_reflects_old_locks(tmp_path, metric_reader):
    import datetime
    from filedge.db import (
        Database, claim_processing, create_audit_tables, insert_pending,
    )
    from filedge.metrics import register_audit_gauges

    reader, meter = metric_reader
    audit_url = f"sqlite:///{tmp_path}/audit.db"
    db = Database(audit_url)
    create_audit_tables(db)
    insert_pending(db, "fresh.csv", "h-fresh-g")
    insert_pending(db, "stale1.csv", "h-stale1-g")
    insert_pending(db, "stale2.csv", "h-stale2-g")
    for h in ["h-fresh-g", "h-stale1-g", "h-stale2-g"]:
        claim_processing(db, h)
    long_ago = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    db.execute(
        "UPDATE etl_file_audit SET claimed_at=? WHERE content_hash IN (?, ?)",
        [long_ago, "h-stale1-g", "h-stale2-g"],
    )
    db.commit()
    db.close()

    register_audit_gauges(meter, audit_db_url=audit_url, stale_minutes=30)

    points = _data_points(reader, "filedge.audit.stale_processing_count")
    assert sum(p.value for p in points) == 2


def test_pending_count_gauge_reads_live_from_audit_db(tmp_path, metric_reader):
    from filedge.db import Database, create_audit_tables, insert_pending
    from filedge.metrics import register_audit_gauges

    reader, meter = metric_reader
    audit_url = f"sqlite:///{tmp_path}/audit.db"
    db = Database(audit_url)
    create_audit_tables(db)
    insert_pending(db, "a.csv", "h-pend-1")
    insert_pending(db, "b.csv", "h-pend-2")
    insert_pending(db, "c.csv", "h-pend-3")
    db.commit()
    db.close()

    register_audit_gauges(meter, audit_db_url=audit_url, stale_minutes=30)

    points = _data_points(reader, "filedge.audit.pending_count")
    assert sum(p.value for p in points) == 3


def test_processing_duration_histogram_records_one_measurement_per_file(tmp_path, metric_reader):
    from filedge.pipeline import run_pipeline
    from filedge.progress import MetricsProgressReporter

    reader, meter = metric_reader
    watched, config, audit = _minimal_pipeline(tmp_path)
    (tmp_path / "watch" / "b.csv").write_text("name,value\nBob,2\n")

    reporter = MetricsProgressReporter(run_id="run-m-dur", meter=meter)
    run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-m-dur")

    points = _data_points(reader, "filedge.file.processing.duration_seconds")
    total_count = sum(p.count for p in points)
    assert total_count == 2  # one measurement per file
    # Every point carries run_id
    assert all(p.attributes["filedge.run_id"] == "run-m-dur" for p in points)
    # Durations are non-negative
    assert all(p.sum >= 0 for p in points)


def test_bytes_ingested_counter_sums_committed_files_only(tmp_path, metric_reader):
    from filedge.pipeline import run_pipeline
    from filedge.progress import MetricsProgressReporter

    reader, meter = metric_reader
    watched, config, audit = _minimal_pipeline(tmp_path)
    bad = tmp_path / "watch" / "broken.csv"
    bad.write_text("name\nNoValue\n")
    good_bytes = (tmp_path / "watch" / "a.csv").stat().st_size

    reporter = MetricsProgressReporter(run_id="run-m-bytes", meter=meter)
    run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-m-bytes")

    # Bytes from the failed file must NOT be counted.
    assert _counter_total(reader, "filedge.bytes.ingested") == good_bytes


def test_failed_file_increments_failed_counter_only(tmp_path, metric_reader):
    from filedge.pipeline import run_pipeline
    from filedge.progress import MetricsProgressReporter

    reader, meter = metric_reader
    watched, config, audit = _minimal_pipeline(tmp_path)
    (tmp_path / "watch" / "broken.csv").write_text("name\nNoValue\n")

    reporter = MetricsProgressReporter(run_id="run-m-fail", meter=meter)
    run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-m-fail")

    assert _counter_total(reader, "filedge.files.committed") == 1  # a.csv
    assert _counter_total(reader, "filedge.files.failed") == 1     # broken.csv


def test_committed_counter_point_carries_run_id_attribute(tmp_path, metric_reader):
    from filedge.pipeline import run_pipeline
    from filedge.progress import MetricsProgressReporter

    reader, meter = metric_reader
    watched, config, audit = _minimal_pipeline(tmp_path)

    reporter = MetricsProgressReporter(run_id="run-m-attr", meter=meter)
    run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-m-attr")

    points = _data_points(reader, "filedge.files.committed")
    assert len(points) == 1
    assert points[0].attributes["filedge.run_id"] == "run-m-attr"
