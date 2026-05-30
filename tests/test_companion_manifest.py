"""The emitter's contract is a round-trip: anything it writes, the existing
Source Manifest reader parses into a valid SourceMetadata. That guarantees an
API-sourced File carries the same audit provenance as a file drop (ADR-0011).
"""

import os

from filedge.companion.manifest import emit_manifest
from filedge.source_manifest import discover_and_parse


def _emit(tmp_path, **overrides):
    data_file = tmp_path / "github-commits-2026-05-01.ndjson"
    data_file.write_text('{"id": 1}\n')
    kwargs = dict(
        source_type="github",
        source_name="github-commits",
        producer="https://example/fetcher",
        run_id="run-abc",
        started_at="2026-05-30T00:00:00+00:00",
        finished_at="2026-05-30T00:01:00+00:00",
        record_count=42,
        source_range={"cursor_param": "since", "from": None, "to": "2026-05-29"},
    )
    kwargs.update(overrides)
    return str(data_file), emit_manifest(str(data_file), **kwargs)


def test_emitted_sidecar_lands_next_to_the_data_file(tmp_path):
    data_file, sidecar = _emit(tmp_path)
    assert sidecar == data_file + ".manifest.json"
    assert os.path.isfile(sidecar)


def test_emitted_manifest_round_trips_through_the_reader(tmp_path):
    data_file, _ = _emit(tmp_path)

    result = discover_and_parse(data_file)

    assert result.found is True
    assert result.error_category is None
    md = result.metadata
    assert md.source_type == "github"
    assert md.source_name == "github-commits"
    assert md.producer == "https://example/fetcher"
    assert md.external_run_id == "run-abc"
    assert md.record_count == 42
    assert md.started_at == "2026-05-30T00:00:00+00:00"
    assert md.finished_at == "2026-05-30T00:01:00+00:00"
    assert md.source_range == {"cursor_param": "since", "from": None, "to": "2026-05-29"}


def test_emitted_manifest_round_trips_without_a_source_range(tmp_path):
    data_file, _ = _emit(tmp_path, source_range=None)

    result = discover_and_parse(data_file)

    assert result.found is True
    assert result.error_category is None
    assert result.metadata.source_range is None
