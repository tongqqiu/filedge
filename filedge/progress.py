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
        )
    )


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
