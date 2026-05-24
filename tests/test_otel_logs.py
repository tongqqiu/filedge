import io
import json
import logging

import pytest

pytest.importorskip("opentelemetry")

from opentelemetry.sdk._logs.export import InMemoryLogExporter  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402


def teardown_function(_fn):
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("filedge"):
            logging.getLogger(name).handlers.clear()
    logging.getLogger().handlers.clear()


def test_should_enable_logs_precedence():
    from filedge.tracing import should_enable_logs

    assert should_enable_logs(cli_flag=None, env_value=None) is False
    assert should_enable_logs(cli_flag=None, env_value="true") is True
    assert should_enable_logs(cli_flag=None, env_value="false") is False
    assert should_enable_logs(cli_flag=True, env_value="false") is True
    assert should_enable_logs(cli_flag=False, env_value="true") is False


def test_otel_log_bridge_exports_structured_attributes_and_keeps_stderr_json():
    from filedge.log import configure_logging, get_logger
    from filedge.tracing import configure_otel_logs

    stream = io.StringIO()
    exporter = InMemoryLogExporter()
    configure_logging(level="INFO", fmt="json", stream=stream)
    configure_otel_logs(enabled=True, log_exporter=exporter)

    log = get_logger("filedge.test")
    log.warning("file_failed", extra={"run_id": "run-1", "file_hash": "abc"})

    stderr_record = json.loads(stream.getvalue().splitlines()[0])
    assert stderr_record["event"] == "file_failed"
    assert stderr_record["run_id"] == "run-1"
    assert stderr_record["file_hash"] == "abc"

    otel_record = exporter.get_finished_logs()[0].log_record
    assert otel_record.body == "file_failed"
    assert otel_record.severity_text == "WARN"
    assert otel_record.attributes["run_id"] == "run-1"
    assert otel_record.attributes["file_hash"] == "abc"


def test_otel_log_bridge_adds_trace_and_span_ids_inside_span():
    from filedge.log import configure_logging, get_logger
    from filedge.tracing import configure_otel_logs

    provider = TracerProvider()
    tracer = provider.get_tracer("filedge.test")
    exporter = InMemoryLogExporter()
    configure_logging(level="INFO", fmt="json", stream=io.StringIO())
    configure_otel_logs(enabled=True, log_exporter=exporter)

    with tracer.start_as_current_span("work") as span:
        get_logger("filedge.test").info("inside_span", extra={"run_id": "run-2"})
        context = span.get_span_context()

    otel_record = exporter.get_finished_logs()[0].log_record
    assert otel_record.trace_id == context.trace_id
    assert otel_record.span_id == context.span_id


def test_otel_log_bridge_disabled_does_not_import_opentelemetry():
    import subprocess
    import sys

    code = (
        "import sys; "
        "from filedge.tracing import configure_otel_logs; "
        "configure_otel_logs(enabled=False); "
        "leaked = [m for m in sys.modules if m == 'opentelemetry' or m.startswith('opentelemetry.')]; "
        "assert not leaked, leaked; "
        "print('OK')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert result.stdout.strip() == "OK"
