"""Structured logging for filedge.

Provides a stdlib-`logging`-based JSON formatter so that operators running
`filedge run` under a scheduler (cron, Airflow, K8s CronJob) get one
machine-parseable line per event, with the Run's `run_id` on every line.
"""
import datetime
import json
import logging
import sys
from typing import IO, Optional

# Standard LogRecord attributes; anything else attached to a record via
# `extra=` is treated as structured payload and merged into the JSON object.
_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.datetime.fromtimestamp(record.created, datetime.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable single-line format for interactive use."""
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(record.created, datetime.UTC).strftime("%H:%M:%S")
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and not k.startswith("_")
        }
        suffix = " ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{ts} {record.levelname:<5} {record.name} {record.getMessage()}"
        return f"{line} {suffix}" if suffix else line


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    stream: Optional[IO[str]] = None,
) -> None:
    """Install a single handler on the `filedge` logger.

    Called once from the CLI; tests pass a `stream` to capture output.
    """
    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter()
    elif fmt == "text":
        formatter = TextFormatter()
    else:
        raise ValueError(f"Unknown log format: {fmt!r} (expected 'json' or 'text')")

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger("filedge")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the `filedge` namespace."""
    if not name.startswith("filedge"):
        name = f"filedge.{name}"
    return logging.getLogger(name)
