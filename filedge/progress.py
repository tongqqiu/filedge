import logging
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class PipelineProgressEvent:
    phase: str
    action: str
    path: Optional[str] = None
    current: Optional[int] = None
    total: Optional[int] = None
    rows: Optional[int] = None
    error: Optional[str] = None
    file_hash: Optional[str] = None
    bytes: Optional[int] = None


ProgressReporter = Callable[[PipelineProgressEvent], None]


def emit_progress(
    reporter: Optional[ProgressReporter],
    phase: str,
    action: str,
    *,
    path: Optional[str] = None,
    current: Optional[int] = None,
    total: Optional[int] = None,
    rows: Optional[int] = None,
    error: Optional[str] = None,
    file_hash: Optional[str] = None,
    bytes: Optional[int] = None,
) -> None:
    if reporter is None:
        return
    reporter(
        PipelineProgressEvent(
            phase=phase,
            action=action,
            path=path,
            current=current,
            total=total,
            rows=rows,
            error=error,
            file_hash=file_hash,
            bytes=bytes,
        )
    )


class TracingProgressReporter:
    """ProgressReporter that emits OpenTelemetry spans for a Run and each File.

    Use as a context manager: `__enter__` starts the `filedge.run` parent span,
    `__exit__` closes it. Imports of `opentelemetry.*` are deferred to the
    constructor so the base install (without the `filedge[otel]` extra) never
    imports OTel and pays zero cost.
    """

    def __init__(self, run_id: str, tracer=None):
        if tracer is None:
            from opentelemetry import trace as _trace
            tracer = _trace.get_tracer("filedge")
        self._tracer = tracer
        self._run_id = run_id
        self._run_span_cm = None
        self._run_span = None
        self._file_span_cms: dict = {}

    def __enter__(self):
        self._run_span_cm = self._tracer.start_as_current_span("filedge.run")
        self._run_span = self._run_span_cm.__enter__()
        self._run_span.set_attribute("filedge.run_id", self._run_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._run_span_cm.__exit__(exc_type, exc, tb)

    def set_run_attributes(self, summary: dict) -> None:
        """Copy selected summary fields onto the open Run span before context exit."""
        if self._run_span is None:
            return
        for key in (
            "files_scanned", "bytes_processed", "rows_committed",
            "committed", "failed", "skipped", "new_files",
            "reclaimed", "retried", "duration_s",
        ):
            if key in summary and summary[key] is not None:
                self._run_span.set_attribute(f"filedge.{key}", summary[key])

    def handle(self, event: PipelineProgressEvent) -> None:
        if event.phase != "loading":
            return
        if event.action == "file_start":
            span_cm = self._tracer.start_as_current_span("filedge.file")
            span = span_cm.__enter__()
            span.set_attribute("filedge.run_id", self._run_id)
            span.set_attribute("filedge.filename", _basename(event.path))
            if event.file_hash is not None:
                span.set_attribute("filedge.file_hash", event.file_hash)
            if event.bytes is not None:
                span.set_attribute("filedge.bytes", event.bytes)
            self._file_span_cms[event.path] = (span_cm, span)
        elif event.action == "file_finish":
            entry = self._file_span_cms.pop(event.path, None)
            if entry is None:
                return
            span_cm, span = entry
            if event.rows is not None:
                span.set_attribute("filedge.rows", event.rows)
            if event.error:
                from opentelemetry.trace import Status, StatusCode
                span.set_status(Status(StatusCode.ERROR, description=event.error))
            span_cm.__exit__(None, None, None)


def _basename(path):
    if path is None:
        return None
    return path.split("/")[-1]


class MetricsProgressReporter:
    """ProgressReporter that emits OpenTelemetry metrics for files processed in a Run.

    Imports of `opentelemetry.*` are deferred so the base install (without the
    `filedge[otel]` extra) never imports OTel.
    """

    def __init__(self, run_id: str, meter=None):
        if meter is None:
            from opentelemetry import metrics as _metrics
            meter = _metrics.get_meter("filedge")
        self._run_id = run_id
        self._committed = meter.create_counter(
            name="filedge.files.committed",
            unit="files",
            description="Files successfully committed during a Run.",
        )
        self._failed = meter.create_counter(
            name="filedge.files.failed",
            unit="files",
            description="Files that failed to load during a Run.",
        )
        self._bytes_ingested = meter.create_counter(
            name="filedge.bytes.ingested",
            unit="By",
            description="Bytes from committed Files (failed Files excluded).",
        )
        self._duration = meter.create_histogram(
            name="filedge.file.processing.duration_seconds",
            unit="s",
            description="Per-File load duration.",
        )
        self._inflight_bytes: dict = {}
        self._inflight_started: dict = {}

    def handle(self, event: PipelineProgressEvent) -> None:
        if event.phase != "loading":
            return
        if event.action == "file_start":
            if event.bytes is not None:
                self._inflight_bytes[event.path] = event.bytes
            import time
            self._inflight_started[event.path] = time.perf_counter()
            return
        if event.action != "file_finish":
            return
        attrs = {"filedge.run_id": self._run_id}
        bytes_for_file = self._inflight_bytes.pop(event.path, 0)
        started = self._inflight_started.pop(event.path, None)
        if started is not None:
            import time
            self._duration.record(time.perf_counter() - started, attributes=attrs)
        if event.error is None:
            self._committed.add(1, attributes=attrs)
            if bytes_for_file:
                self._bytes_ingested.add(bytes_for_file, attributes=attrs)
        else:
            self._failed.add(1, attributes=attrs)


class LoggingProgressReporter:
    """ProgressReporter that emits one structured log line per PipelineProgressEvent.

    Composable with RichPipelineProgress — both can be attached to the same Run.
    Each log line carries the Run's `run_id` so operators can correlate events.
    """

    def __init__(self, logger: logging.Logger, run_id: str):
        self._logger = logger
        self._run_id = run_id

    def handle(self, event: PipelineProgressEvent) -> None:
        extra = {"run_id": self._run_id, "phase": event.phase, "action": event.action}
        if event.path is not None:
            extra["path"] = event.path
        if event.rows is not None:
            extra["rows"] = event.rows
        if event.total is not None:
            extra["total"] = event.total
        if event.current is not None:
            extra["current"] = event.current
        level = logging.ERROR if event.error else logging.INFO
        if event.error:
            extra["error"] = event.error
        self._logger.log(level, f"pipeline.{event.phase}.{event.action}", extra=extra)


class RichPipelineProgress:
    def __init__(self, console):
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
        )

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[status]}"),
            console=console,
            transient=False,
        )
        self._tasks = {}
        self._current_file = None
        self._current_rows = 0

    def __enter__(self):
        self._progress.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._progress.__exit__(exc_type, exc, tb)

    def handle(self, event: PipelineProgressEvent) -> None:
        if event.action == "start":
            self._tasks[event.phase] = self._progress.add_task(
                _phase_label(event.phase),
                total=event.total or 0,
                status="",
            )
            return

        task_id = self._tasks.get(event.phase)
        if task_id is None:
            return

        if event.action == "file_start":
            self._current_file = event.path
            self._current_rows = 0
            self._progress.update(task_id, status=_file_status(event.path, 0))
        elif event.action == "rows":
            self._current_file = event.path or self._current_file
            self._current_rows = event.rows or self._current_rows
            self._progress.update(
                task_id,
                status=_file_status(self._current_file, self._current_rows),
            )
        elif event.action == "advance":
            status = _file_status(self._current_file, self._current_rows)
            self._progress.update(task_id, advance=1, status=status)
        elif event.action == "file_finish":
            self._current_file = event.path or self._current_file
            self._current_rows = event.rows or self._current_rows
            status = _file_status(self._current_file, self._current_rows)
            if event.error:
                status = f"{status} failed"
            self._progress.update(task_id, status=status)
        elif event.action == "finish":
            self._progress.update(task_id, completed=event.total, status="")


def _phase_label(phase: str) -> str:
    return {
        "hashing": "Hashing files",
        "registering": "Registering files",
        "loading": "Loading files",
    }.get(phase, phase)


def _file_status(path: Optional[str], rows: int) -> str:
    if not path:
        return ""
    if rows:
        return f"{path}: {rows:,} rows"
    return path
