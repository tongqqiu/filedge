from filedge.config import ColumnMapping, PipelineConfig
from filedge.db import find_file_by_hash
from filedge.file_registration import register_files


def _config() -> PipelineConfig:
    return PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="string", required=True),
        ],
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
