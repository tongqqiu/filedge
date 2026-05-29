"""Tests for the Pipeline Config Draft (#146) — the headless, editable core of
the Authoring Workflow. Everything here drives the draft through its public
interface from Python alone; no UI is involved."""

import pytest

from filedge.authoring_draft import ColumnDraft, PipelineConfigDraft
from filedge.config import config_from_dict, load_config


def _csv(tmp_path, body, name="sample.csv"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_from_sample_exposes_inferred_columns_with_confidence(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n2,Bob\n3,Carol\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    by_name = {c.source: c for c in draft.columns}
    assert set(by_name) == {"id", "name"}
    assert by_name["id"].type == "integer"
    assert by_name["name"].type == "string"
    # Confidence Tier rides along as read-only evidence.
    assert by_name["id"].confidence == "high"
    # dest defaults to the source name; columns are required by default.
    assert by_name["id"].dest == "id"
    assert by_name["id"].required is True


def test_from_sample_surfaces_low_and_ambiguous_tiers(tmp_path):
    # `amount` has a null -> low; `blank` is entirely empty -> ambiguous.
    src = _csv(tmp_path, "amount,blank\n10,\n,\n20,\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    by_name = {c.source: c for c in draft.columns}
    assert by_name["amount"].confidence == "low"
    assert by_name["blank"].confidence == "ambiguous"


def test_edit_column_changes_authored_fields(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    draft.edit_column(
        "name", new_source="name", dest="full_name", type="string", required=False
    )
    draft.edit_column("id", dest="person_id")

    by_dest = {c.dest: c for c in draft.columns}
    assert by_dest["full_name"].required is False
    assert by_dest["person_id"].source == "id"


def test_edit_column_rejects_invalid_type_without_mutating(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    with pytest.raises(ValueError):
        draft.edit_column("id", type="int64")
    # The rejected edit left the column unchanged.
    assert draft.column("id").type == "integer"


def test_edit_unknown_column_raises(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    with pytest.raises(KeyError):
        draft.edit_column("nope", dest="x")


def test_to_config_round_trips_via_config_from_dict(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n2,Bob\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    draft.edit_column("name", required=False)

    cfg = draft.to_config()  # config_from_dict under the hood — must not raise
    assert cfg.dest_table == "people"
    assert cfg.format == "csv"
    assert cfg.write_mode == "append"
    by_source = {c.source: c for c in cfg.columns}
    assert by_source["id"].type == "integer"
    assert by_source["name"].required is False


def test_to_config_dict_round_trips_through_file_loader(tmp_path):
    import yaml

    src = _csv(tmp_path, "id,name\n1,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    cfg_path = tmp_path / "pipeline.yaml"
    cfg_path.write_text(yaml.safe_dump(draft.to_config_dict()))
    cfg = load_config(str(cfg_path))  # the exact path the Operator CLI uses
    assert cfg.dest_table == "people"
    assert [c.source for c in cfg.columns] == ["id", "name"]


def test_non_csv_sample_is_rejected_in_this_slice(tmp_path):
    src = _csv(tmp_path, "id\n1\n", name="data.ndjson")
    with pytest.raises(ValueError, match="CSV"):
        PipelineConfigDraft.from_sample(src, "t", fmt="ndjson")


def test_config_from_dict_matches_load_config(tmp_path):
    # Parity guard for the load_config refactor: dict-in == file-in.
    data = {
        "format": "csv",
        "dest_table": "t",
        "columns": [{"source": "id", "dest": "id", "type": "integer"}],
    }
    import yaml

    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    assert config_from_dict(data) == load_config(str(p))


def test_column_draft_defaults():
    c = ColumnDraft(source="a", dest="a", type="string")
    assert c.required is True
    assert c.confidence == "high"
    assert c.notes == []
