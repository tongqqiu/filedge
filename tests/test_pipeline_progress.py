import io
import json
import logging

from filedge.pipeline import run_pipeline


def _write_config(path, dest_db_url):
    path.write_text(
        f"format: csv\n"
        f"dest_table: items\n"
        f"retry_cap: 3\n"
        f"batch_size: 100\n"
        f"stale_timeout_minutes: 30\n"
        f"connector:\n"
        f"  type: sqlite\n"
        f"  url: {dest_db_url}\n"
        f"columns:\n"
        f"  - source: name\n"
        f"    dest: name\n"
        f"    type: string\n"
        f"    required: true\n"
        f"  - source: value\n"
        f"    dest: value\n"
        f"    type: string\n"
        f"    required: true\n"
    )


def test_run_pipeline_emits_file_level_progress(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    (watched / "b.csv").write_text("name,value\nBob,2\n")
    config_file = tmp_path / "pipeline.yaml"
    _write_config(config_file, f"sqlite:///{tmp_path}/dest.db")
    events = []

    result = run_pipeline(
        str(watched),
        str(config_file),
        f"sqlite:///{tmp_path}/audit.db",
        progress=events.append,
    )

    assert result["committed"] == 2
    phase_starts = [
        (event.phase, event.total)
        for event in events
        if event.action == "start"
    ]
    assert phase_starts == [
        ("hashing", 2),
        ("registering", 2),
        ("loading", 2),
    ]
    assert sum(
        1
        for event in events
        if event.phase == "loading" and event.action == "file_start"
    ) == 2
    assert [
        event.rows
        for event in events
        if event.phase == "loading" and event.action == "file_finish"
    ] == [1, 1]


def test_logging_progress_reporter_emits_one_json_line_per_event(tmp_path):
    """LoggingProgressReporter subscribes to the Run event stream and emits a
    JSON log line per PipelineProgressEvent, each carrying the Run's run_id."""
    from filedge.log import configure_logging, get_logger
    from filedge.progress import LoggingProgressReporter

    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    config_file = tmp_path / "pipeline.yaml"
    _write_config(config_file, f"sqlite:///{tmp_path}/dest.db")
    audit_db_url = f"sqlite:///{tmp_path}/audit.db"

    buf = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=buf)
    reporter = LoggingProgressReporter(get_logger("filedge.pipeline"), run_id="run-abc")

    raw_events = []

    def tee(event):
        raw_events.append(event)
        reporter.handle(event)

    run_pipeline(
        str(watched), str(config_file), audit_db_url,
        progress=tee, run_id="run-abc",
    )

    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    # One log line per emitted PipelineProgressEvent.
    assert len(records) == len(raw_events)
    # Every log line carries the Run's run_id.
    assert all(r["run_id"] == "run-abc" for r in records)
    # Phase and action are propagated so operators can filter.
    assert any(r["phase"] == "loading" and r["action"] == "file_finish" for r in records)

    # Clean up handlers so other tests aren't affected.
    logging.getLogger("filedge").handlers.clear()


def test_loading_progress_counts_only_pending_files(tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    (watched / "a.csv").write_text("name,value\nAlice,1\n")
    config_file = tmp_path / "pipeline.yaml"
    _write_config(config_file, f"sqlite:///{tmp_path}/dest.db")
    audit_db_url = f"sqlite:///{tmp_path}/audit.db"

    run_pipeline(str(watched), str(config_file), audit_db_url)
    events = []
    result = run_pipeline(
        str(watched),
        str(config_file),
        audit_db_url,
        progress=events.append,
    )

    assert result["committed"] == 0
    loading_start = next(
        event
        for event in events
        if event.phase == "loading" and event.action == "start"
    )
    assert loading_start.total == 0
