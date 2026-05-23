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
