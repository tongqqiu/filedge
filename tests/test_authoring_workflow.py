"""End-to-end tests for the Authoring Workflow behind the Textual UI."""

import os

import pytest

from filedge.authoring_draft import ColumnDraft
from filedge.authoring_workflow import AuthoringWorkflow
from filedge.pipeline_registry import load_registry


def _file(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def _acknowledge_all(workflow):
    for review in workflow.confidence_reviews():
        workflow.acknowledge_confidence_tier(review.source)


def test_authoring_workflow_happy_path_generates_artifacts(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n2,Bob\n")

    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    assert workflow.fmt == "csv"
    assert workflow.preview_rows == [
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Bob"},
    ]
    assert [c.source for c in workflow.draft.columns] == ["id", "name"]

    report = workflow.validate()
    assert report.ok

    _acknowledge_all(workflow)
    result = workflow.generate()
    assert os.path.isfile(result.config_path)
    assert os.path.isfile(result.runbook_path)
    assert load_registry(str(workspace)).entries[0].id == "people"
    assert any("filedge healthcheck" in cmd for cmd in workflow.suggested_commands())


def test_authoring_workflow_blocks_generation_when_validation_is_red(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id\nnot-an-int\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("id", type="integer")

    report = workflow.validate()

    assert not report.ok
    _acknowledge_all(workflow)
    with pytest.raises(ValueError, match="green"):
        workflow.generate()


def test_authoring_workflow_does_not_run_ingestion_or_touch_audit_db(
    tmp_path, monkeypatch
):
    import filedge.db as db
    import filedge.pipeline as pipeline

    def _boom(*args, **kwargs):
        raise AssertionError("Authoring UI must not run ingestion or touch Audit DB")

    monkeypatch.setattr(pipeline, "run_pipeline", _boom)
    monkeypatch.setattr(db, "Database", _boom)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")

    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    _acknowledge_all(workflow)
    workflow.generate()


def test_authoring_workflow_does_not_store_secret_material(tmp_path, monkeypatch):
    secret = "postgresql://user:secret@host/db"
    monkeypatch.setenv("PEOPLE_AUDIT_DB_URL", secret)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")

    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    _acknowledge_all(workflow)
    result = workflow.generate()

    assert secret not in open(result.runbook_path).read()
    assert secret not in open(result.registry_path).read()


def test_authoring_workflow_supports_fixed_width_manual_layout(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.dat", "001Alice\n002Bob  \n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
        fmt="fixed_width",
    )

    assert workflow.draft is None
    workflow.set_fixed_width_layout(
        [
            ColumnDraft("id", "id", "integer", start=1, width=3),
            ColumnDraft("name", "name", "string", start=4, width=5),
        ]
    )

    assert workflow.preview_rows[0] == {"id": "001", "name": "Alice"}
    assert workflow.validate().ok


def test_authoring_workflow_auto_detects_ndjson(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "events.ndjson", '{"id": 1, "payload": {"x": 2}}\n')

    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="events",
    )

    assert workflow.fmt == "ndjson"
    assert any(
        "nested object" in note
        for note in workflow.draft.column("payload").notes
    )
    payload_review = next(
        r for r in workflow.confidence_reviews() if r.source == "payload"
    )
    assert "notes=nested object" in payload_review.evidence


def test_authoring_workflow_supports_parquet_schema(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = tmp_path / "events.parquet"
    table = pa.table({"id": [1, 2], "name": ["Alice", "Bob"]})
    pq.write_table(table, src)

    workflow = AuthoringWorkflow.start(
        file=str(src),
        workspace=str(workspace),
        dest_table="events",
    )

    assert workflow.fmt == "parquet"
    assert workflow.draft.column("id").type == "integer"


def test_authoring_workflow_supports_excel_sheet_picker(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["id", "name"])
    ws.append([1, "Alice"])
    other = wb.create_sheet("Customers")
    other.append(["customer_id"])
    other.append([10])
    wb.save(src)

    workflow = AuthoringWorkflow.start(
        file=str(src),
        workspace=str(workspace),
        dest_table="orders",
    )

    assert workflow.excel_sheets == ["Orders", "Customers"]
    assert workflow.draft.column("id").type == "integer"

    workflow.choose_sheet("Customers")

    assert [c.source for c in workflow.draft.columns] == ["customer_id"]


def test_authoring_workflow_blocks_generation_until_confidence_tiers_acknowledged(
    tmp_path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n2,Bob\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    reviews = workflow.confidence_reviews()

    assert [r.source for r in reviews] == ["name"]
    assert reviews[0].confidence == "ambiguous"
    assert "null_count=0" in reviews[0].evidence
    assert workflow.unacknowledged_confidence_reviews() == reviews
    with pytest.raises(ValueError, match="Confidence Tier"):
        workflow.generate()

    acknowledged = workflow.acknowledge_confidence_tier("name")

    assert acknowledged.acknowledged is True
    assert workflow.unacknowledged_confidence_reviews() == []


def test_authoring_workflow_records_confidence_acknowledgements_in_runbook(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n2,Bob\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.acknowledge_confidence_tier("name")

    result = workflow.generate()
    runbook = open(result.runbook_path).read()

    assert "Source `name` -> destination `name`" in runbook
    assert "accepted `ambiguous` Confidence Tier" in runbook
    assert "null_count=0" in runbook


def test_authoring_workflow_rejects_non_risky_confidence_acknowledgement(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id\n1\n2\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    with pytest.raises(ValueError, match="Confidence Tier"):
        workflow.acknowledge_confidence_tier("id")


def test_authoring_workflow_rejects_unknown_format_extension(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "events.data", "id\n1\n")

    with pytest.raises(ValueError, match="Cannot detect format"):
        AuthoringWorkflow.start(
            file=src,
            workspace=str(workspace),
            dest_table="events",
        )


def test_authoring_workflow_format_override_rebuilds_draft(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "events.data", '{"id": 1}\n')
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="events",
        fmt="csv",
    )

    workflow.choose_format("ndjson")

    assert workflow.fmt == "ndjson"
    assert workflow.preview_rows == [{"id": 1}]


def test_authoring_workflow_rejects_sheet_picker_for_non_excel(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id\n1\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    with pytest.raises(ValueError, match="excel"):
        workflow.choose_sheet("Orders")


def test_authoring_workflow_rejects_fixed_width_layout_for_other_formats(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id\n1\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    with pytest.raises(ValueError, match="fixed_width"):
        workflow.set_fixed_width_layout(
            [ColumnDraft("id", "id", "integer", start=1, width=3)]
        )


def test_authoring_workflow_requires_draft_for_validation_and_generation(tmp_path):
    workflow = AuthoringWorkflow(
        file=str(tmp_path / "missing.txt"),
        workspace=str(tmp_path),
        dest_table="people",
        fmt="fixed_width",
    )

    assert workflow.suggested_commands() == []
    assert workflow._audit_db_ref() == ""
    with pytest.raises(ValueError, match="Pipeline Config Draft"):
        workflow.validate()
    with pytest.raises(ValueError, match="Pipeline Config Draft"):
        workflow.generate()
