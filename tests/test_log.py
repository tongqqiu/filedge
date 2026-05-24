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
