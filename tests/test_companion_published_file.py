"""Publishing a companion File owns staging, manifest, locked promotion order."""

import os

from filedge.companion.published_file import PublishRequest, publish_file
from filedge.source_manifest import discover_and_parse


def test_publish_file_promotes_data_and_manifest_then_drains_staging(tmp_path):
    staging = tmp_path / "staging"
    landing = tmp_path / "landing"
    state = tmp_path / "state"

    published = publish_file(
        PublishRequest(
            records=[{"id": 1, "updated_at": "2026-05-01"}],
            staging_dir=str(staging),
            watched_directory=str(landing),
            state_dir=str(state),
            source_name="commits",
            source_type="github",
            producer="test",
            started_at="2026-05-30T00:00:00+00:00",
            finished_at="2026-05-30T00:01:00+00:00",
            from_cursor=None,
            to_cursor="2026-05-01",
            source_range={"cursor_param": "since", "from": None, "to": "2026-05-01"},
        )
    )

    assert published.data_path.startswith(str(landing))
    assert published.sidecar_path == published.data_path + ".manifest.json"
    assert os.listdir(staging) == []
    result = discover_and_parse(published.data_path)
    assert result.error_category is None
    assert result.metadata.source_name == "commits"
    assert result.metadata.source_range == {
        "cursor_param": "since",
        "from": None,
        "to": "2026-05-01",
    }
