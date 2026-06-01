import json

from filedge.config import ColumnMapping, PipelineConfig
from filedge.db import find_file_by_hash
from filedge.file_registration import register_files


def _config(source_manifest: str = "optional") -> PipelineConfig:
    return PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="string", required=True),
        ],
        source_manifest=source_manifest,
    )


def test_file_registration_discovers_files_and_returns_load_candidates(db, tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    alpha = watched / "alpha.csv"
    beta = watched / "beta.csv"
    alpha.write_text("name,value\nAlice,1\n")
    beta.write_text("name,value\nBob,2\n")
    events = []

    result = register_files(str(watched), _config(), db, progress=events.append)

    assert result.new_files == 2
    assert result.failed_pre_load == 0
    assert result.skipped == 0
    assert result.bytes_processed == alpha.stat().st_size + beta.stat().st_size
    assert [candidate.path for candidate in result.load_candidates] == [
        str(alpha),
        str(beta),
    ]
    assert [candidate.size for candidate in result.load_candidates] == [
        alpha.stat().st_size,
        beta.stat().st_size,
    ]

    records = [
        find_file_by_hash(db, candidate.content_hash)
        for candidate in result.load_candidates
    ]
    assert [(record.filename, record.state) for record in records] == [
        ("alpha.csv", "PENDING"),
        ("beta.csv", "PENDING"),
    ]
    assert [
        (event.phase, event.action, event.total)
        for event in events
        if event.action in {"start", "finish"}
    ] == [
        ("hashing", "start", 2),
        ("hashing", "finish", 2),
        ("registering", "start", 2),
        ("registering", "finish", 2),
    ]


def test_watched_dir_missing_uses_filesystem_isdir_for_remote():
    from filedge.file_registration import _watched_dir_missing

    class FakeFS:
        def __init__(self, present):
            self._present = present

        def isdir(self, path):
            return self._present

    assert _watched_dir_missing(FakeFS(present=False), "s3://bucket/missing") is True
    assert _watched_dir_missing(FakeFS(present=True), "s3://bucket/landing") is False


def test_missing_watched_dir_is_a_clean_noop(db, tmp_path):
    missing = tmp_path / "not-created-yet"
    assert not missing.exists()

    result = register_files(str(missing), _config(), db)

    assert result.new_files == 0
    assert result.failed_pre_load == 0
    assert result.skipped == 0
    assert result.load_candidates == []


def test_required_manifest_policy_fails_before_load_candidates(db, tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    data_file = watched / "missing-manifest.csv"
    data_file.write_text("name,value\nAlice,1\n")

    result = register_files(str(watched), _config(source_manifest="required"), db)

    assert result.new_files == 1
    assert result.failed_pre_load == 1
    assert result.load_candidates == []
    record = find_file_by_hash(db, result.pre_load_failures[0].content_hash)
    assert record.filename == "missing-manifest.csv"
    assert record.state == "FAILED"
    assert "manifest_missing" in record.error_message


def test_disabled_manifest_policy_ignores_valid_sidecar(db, tmp_path):
    watched = tmp_path / "watch"
    watched.mkdir()
    data_file = watched / "direct.csv"
    data_file.write_text("name,value\nAlice,1\n")
    (watched / "direct.csv.manifest.json").write_text(json.dumps({
        "producer": "https://example.com/fetcher",
        "run": {"runId": "run-1"},
        "job": {"namespace": "api", "name": "stripe.charges"},
    }))

    result = register_files(str(watched), _config(source_manifest="disabled"), db)

    assert result.failed_pre_load == 0
    assert len(result.load_candidates) == 1
    record = find_file_by_hash(db, result.load_candidates[0].content_hash)
    assert record.state == "PENDING"
    assert record.source_type is None
    assert record.source_name is None
