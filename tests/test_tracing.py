"""Tests for OTel tracing support (filedge.progress.TracingProgressReporter).

Skipped when the `otel` extra is not installed.
"""
import pytest

pytest.importorskip("opentelemetry")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


@pytest.fixture
def span_exporter():
    """Return an InMemorySpanExporter wired to a local TracerProvider.

    Yields the exporter and a tracer bound to that provider so the test never
    mutates global OTel state.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("filedge.test")
    yield exporter, tracer
    provider.shutdown()


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


def test_should_enable_tracing_precedence():
    """CLI flag wins over env; env alone enables; default is off."""
    from filedge.tracing import should_enable_tracing

    assert should_enable_tracing(cli_flag=None, env_value=None) is False
    assert should_enable_tracing(cli_flag=None, env_value="") is False
    assert should_enable_tracing(cli_flag=None, env_value="true") is True
    assert should_enable_tracing(cli_flag=None, env_value="false") is False
    assert should_enable_tracing(cli_flag=True, env_value=None) is True
    # CLI flag wins over env in both directions
    assert should_enable_tracing(cli_flag=True, env_value="false") is True
    assert should_enable_tracing(cli_flag=False, env_value="true") is False


def test_cli_run_with_otel_traces_flag_completes_without_crash(tmp_path):
    """Smoke test: --otel-traces wires through CLI → tracer init → reporter
    composition → Run, and the Run still completes successfully."""
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
        "--otel-traces",
    ], env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"})

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["committed"] == 1


def test_base_imports_do_not_pull_in_opentelemetry():
    """Importing filedge.progress and filedge.cli must not import opentelemetry.

    Guards the optional-extra contract: users who don't `pip install filedge[otel]`
    and don't enable tracing pay zero OTel cost.
    """
    import subprocess
    import sys

    code = (
        "import sys; "
        "import filedge.progress, filedge.cli; "
        "leaked = [m for m in sys.modules if m == 'opentelemetry' or m.startswith('opentelemetry.')]; "
        "assert not leaked, f'opentelemetry imported by base modules: {leaked}'; "
        "print('OK')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert result.stdout.strip() == "OK"


def test_tracing_reporter_emits_parent_run_span(tmp_path, span_exporter):
    from filedge.pipeline import run_pipeline
    from filedge.progress import TracingProgressReporter

    exporter, tracer = span_exporter
    watched, config, audit = _minimal_pipeline(tmp_path)

    with TracingProgressReporter(run_id="run-trace-1", tracer=tracer) as reporter:
        run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-trace-1")

    spans = exporter.get_finished_spans()
    run_spans = [s for s in spans if s.name == "filedge.run"]
    assert len(run_spans) == 1
    assert run_spans[0].attributes["filedge.run_id"] == "run-trace-1"


def test_tracing_reporter_copies_summary_onto_run_span(tmp_path, span_exporter):
    from filedge.pipeline import run_pipeline
    from filedge.progress import TracingProgressReporter

    exporter, tracer = span_exporter
    watched, config, audit = _minimal_pipeline(tmp_path)
    (tmp_path / "watch" / "b.csv").write_text("name,value\nBob,2\nCarol,3\n")

    with TracingProgressReporter(run_id="run-trace-sum", tracer=tracer) as reporter:
        result = run_pipeline(
            watched, config, audit, progress=reporter.handle, run_id="run-trace-sum"
        )
        reporter.set_run_attributes(result)

    run_span = next(s for s in exporter.get_finished_spans() if s.name == "filedge.run")
    assert run_span.attributes["filedge.files_scanned"] == 2
    assert run_span.attributes["filedge.bytes_processed"] == result["bytes_processed"]
    assert run_span.attributes["filedge.rows_committed"] == 3
    assert run_span.attributes["filedge.committed"] == 2
    assert run_span.attributes["filedge.failed"] == 0


def test_tracing_reporter_records_file_hash_and_bytes_on_file_span(tmp_path, span_exporter):
    from filedge.pipeline import run_pipeline
    from filedge.progress import TracingProgressReporter

    exporter, tracer = span_exporter
    watched, config, audit = _minimal_pipeline(tmp_path)
    file_path = tmp_path / "watch" / "a.csv"
    expected_bytes = file_path.stat().st_size

    with TracingProgressReporter(run_id="run-trace-bh", tracer=tracer) as reporter:
        run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-trace-bh")

    file_span = next(
        s for s in exporter.get_finished_spans()
        if s.name == "filedge.file" and s.attributes["filedge.filename"] == "a.csv"
    )
    assert file_span.attributes["filedge.bytes"] == expected_bytes
    # SHA-256 hex string is 64 chars
    file_hash = file_span.attributes["filedge.file_hash"]
    assert isinstance(file_hash, str) and len(file_hash) == 64


def test_tracing_reporter_marks_failed_file_span_with_error_status(tmp_path, span_exporter):
    from opentelemetry.trace import StatusCode
    from filedge.pipeline import run_pipeline
    from filedge.progress import TracingProgressReporter

    exporter, tracer = span_exporter
    watched, config, audit = _minimal_pipeline(tmp_path)
    # Add a file missing the required `value` column → will FAIL.
    (tmp_path / "watch" / "broken.csv").write_text("name\nMissingValue\n")

    with TracingProgressReporter(run_id="run-trace-3", tracer=tracer) as reporter:
        run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-trace-3")

    file_spans = [s for s in exporter.get_finished_spans() if s.name == "filedge.file"]
    by_filename = {s.attributes["filedge.filename"]: s for s in file_spans}

    ok_span = by_filename["a.csv"]
    assert ok_span.status.status_code == StatusCode.OK or ok_span.status.status_code == StatusCode.UNSET
    assert ok_span.attributes["filedge.rows"] == 1

    failed_span = by_filename["broken.csv"]
    assert failed_span.status.status_code == StatusCode.ERROR
    assert "value" in (failed_span.status.description or "") or \
        "Missing" in (failed_span.status.description or "")


def test_tracing_reporter_emits_child_span_per_file_nested_under_run(tmp_path, span_exporter):
    from filedge.pipeline import run_pipeline
    from filedge.progress import TracingProgressReporter

    exporter, tracer = span_exporter
    watched, config, audit = _minimal_pipeline(tmp_path)
    (tmp_path / "watch" / "b.csv").write_text("name,value\nBob,2\n")

    with TracingProgressReporter(run_id="run-trace-2", tracer=tracer) as reporter:
        run_pipeline(watched, config, audit, progress=reporter.handle, run_id="run-trace-2")

    spans = exporter.get_finished_spans()
    run_span = next(s for s in spans if s.name == "filedge.run")
    file_spans = [s for s in spans if s.name == "filedge.file"]

    assert len(file_spans) == 2
    filenames = {s.attributes["filedge.filename"] for s in file_spans}
    assert filenames == {"a.csv", "b.csv"}
    # Each file span carries the Run's run_id and is nested under the parent.
    for fs in file_spans:
        assert fs.attributes["filedge.run_id"] == "run-trace-2"
        assert fs.parent is not None
        assert fs.parent.span_id == run_span.context.span_id
