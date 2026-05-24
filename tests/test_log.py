"""Tests for filedge.log — the structured logging module for production observability."""
import io
import json
import logging


def _capture_logger(name: str, level: str = "INFO", fmt: str = "json"):
    """Wire a JSON-formatted logger to a StringIO buffer and return (logger, buffer)."""
    from filedge.log import configure_logging, get_logger

    buf = io.StringIO()
    configure_logging(level=level, fmt=fmt, stream=buf)
    log = get_logger(name)
    return log, buf


def teardown_function(_fn):
    # Each test wires its own handler; clear filedge.* loggers between tests.
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("filedge"):
            logging.getLogger(name).handlers.clear()
    logging.getLogger().handlers.clear()


def test_json_logger_emits_parseable_line():
    log, buf = _capture_logger("filedge.test")
    log.info("file_committed", extra={"run_id": "r1", "file_hash": "abc"})

    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "file_committed"
    assert record["level"] == "INFO"
    assert record["logger"] == "filedge.test"
    assert record["run_id"] == "r1"
    assert record["file_hash"] == "abc"
    assert "ts" in record


def test_json_logger_respects_level():
    log, buf = _capture_logger("filedge.test", level="WARNING")
    log.info("should_be_suppressed")
    log.warning("should_appear")

    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    events = [json.loads(line)["event"] for line in lines]
    assert events == ["should_appear"]


def test_json_logger_includes_exception_text():
    log, buf = _capture_logger("filedge.test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed_to_load")

    record = json.loads(buf.getvalue().splitlines()[0])
    assert record["event"] == "failed_to_load"
    assert "RuntimeError: boom" in record["exc_info"]


def test_text_formatter_renders_human_readable_line():
    log, buf = _capture_logger("filedge.test", fmt="text")
    log.info("file_committed", extra={"run_id": "r1", "path": "/tmp/a.csv"})

    line = buf.getvalue().strip()
    assert "INFO" in line
    assert "filedge.test" in line
    assert "file_committed" in line
    assert "run_id=r1" in line
    assert "path=/tmp/a.csv" in line


def test_configure_logging_rejects_unknown_format():
    import pytest
    from filedge.log import configure_logging

    with pytest.raises(ValueError, match="Unknown log format"):
        configure_logging(fmt="xml")


def test_get_logger_normalizes_bare_name_under_filedge_namespace():
    from filedge.log import get_logger

    log = get_logger("worker")
    assert log.name == "filedge.worker"

    log = get_logger("filedge.pipeline")
    assert log.name == "filedge.pipeline"
