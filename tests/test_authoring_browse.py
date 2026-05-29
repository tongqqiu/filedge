"""Pipeline Registry browse-and-pick screen tests (#179)."""

import importlib.util
import shutil

import pytest

from filedge.authoring_browse import (
    NEW_PIPELINE_SENTINEL,
    PipelineBrowseEntry,
    list_browse_entries,
)
from filedge.authoring_workflow import AuthoringWorkflow


def _author_pipeline(workspace, sample_dir, name: str):
    sample = sample_dir / f"{name}.csv"
    sample.write_text("id,name\n1,Alice\n")
    wf = AuthoringWorkflow.start(
        file=str(sample), workspace=str(workspace), dest_table=name
    )
    wf.validate()
    for review in wf.confidence_reviews():
        wf.acknowledge_confidence_tier(review.source)
    wf.generate()
    return wf


def test_list_browse_entries_summarises_each_pipeline(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _author_pipeline(workspace, tmp_path, "people")
    _author_pipeline(workspace, tmp_path, "orders")

    entries = list_browse_entries(str(workspace))

    assert [e.id for e in entries] == ["people", "orders"]
    for entry in entries:
        assert entry.openable is True
        assert entry.format == "csv"
        assert entry.connector_type == "sqlite"
        assert entry.last_authored is not None
        assert entry.folder == f"pipelines/{entry.id}"


def test_missing_folder_is_listed_as_unopenable(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _author_pipeline(workspace, tmp_path, "people")
    shutil.rmtree(workspace / "pipelines" / "people")

    entries = list_browse_entries(str(workspace))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.openable is False
    assert "missing on disk" in entry.message
    assert entry.format is None


def test_list_browse_entries_errors_when_no_registry(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(FileNotFoundError):
        list_browse_entries(str(workspace))


def test_unreadable_pipeline_yaml_is_unopenable(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _author_pipeline(workspace, tmp_path, "people")
    (workspace / "pipelines" / "people" / "pipeline.yaml").write_text(": :\nnot: [valid")

    entries = list_browse_entries(str(workspace))

    assert entries[0].openable is False
    assert "unreadable" in entries[0].message


@pytest.mark.skipif(
    importlib.util.find_spec("textual") is None,
    reason="textual extra not installed",
)
def test_browse_app_returns_selected_folder(tmp_path):
    import asyncio

    from filedge.authoring_browse import PipelineBrowseApp

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _author_pipeline(workspace, tmp_path, "people")
    _author_pipeline(workspace, tmp_path, "orders")
    entries = list_browse_entries(str(workspace))

    async def run():
        app = PipelineBrowseApp(entries)
        async with app.run_test() as pilot:
            await pilot.press("down")  # move from row 0 (people) to row 1 (orders)
            await pilot.press("enter")
        return app.selected_folder

    selected = asyncio.run(run())
    assert selected == "pipelines/orders"


@pytest.mark.skipif(
    importlib.util.find_spec("textual") is None,
    reason="textual extra not installed",
)
def test_browse_app_new_pipeline_returns_sentinel(tmp_path):
    import asyncio

    from filedge.authoring_browse import PipelineBrowseApp

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _author_pipeline(workspace, tmp_path, "people")
    entries = list_browse_entries(str(workspace))

    async def run():
        app = PipelineBrowseApp(entries)
        async with app.run_test() as pilot:
            await pilot.press("n")
        return app.selected_folder

    selected = asyncio.run(run())
    assert selected == NEW_PIPELINE_SENTINEL


@pytest.mark.skipif(
    importlib.util.find_spec("textual") is None,
    reason="textual extra not installed",
)
def test_browse_app_blocks_opening_unopenable_pipeline(tmp_path):
    import asyncio

    from filedge.authoring_browse import PipelineBrowseApp

    entries = [
        PipelineBrowseEntry(
            id="people",
            folder="pipelines/people",
            last_authored=None,
            format=None,
            connector_type=None,
            openable=False,
            message="Folder 'pipelines/people' is missing on disk.",
        )
    ]

    async def run():
        app = PipelineBrowseApp(entries)
        async with app.run_test() as pilot:
            await pilot.press("enter")
            status = str(app.query_one("#status").render())
        return app.selected_folder, status

    selected, status = asyncio.run(run())
    assert selected is None
    assert "cannot be opened" in status
