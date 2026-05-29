"""End-to-end tests for the Authoring Workflow behind the Textual UI."""

import os

import pytest
import yaml

from filedge.authoring_draft import ColumnDraft
from filedge.authoring_validation import SCOPE_WRITE_MODE
from filedge.authoring_workflow import AuthoringWorkflow
from filedge.config import load_config
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


def test_authoring_workflow_blocks_cdc_generation_until_settings_are_valid(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,op,updated_at,name\n1,insert,10,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    workflow.choose_write_mode("cdc")
    workflow.acknowledge_confidence_tier("op")
    workflow.acknowledge_confidence_tier("name")

    report = workflow.validate()
    failures = [f.message for f in report.findings_in(SCOPE_WRITE_MODE) if not f.ok]

    assert not report.ok
    assert any("business key" in message for message in failures)
    assert any("sequence column" in message for message in failures)
    with pytest.raises(ValueError, match="green"):
        workflow.generate()


def test_authoring_workflow_generates_cdc_pipeline_yaml(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,op,updated_at,name\n1,insert,10,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    workflow.choose_write_mode("cdc")
    workflow.set_cdc_settings(business_keys=["id"], sequence_by="updated_at")
    workflow.acknowledge_confidence_tier("op")
    workflow.acknowledge_confidence_tier("name")

    result = workflow.generate()
    config = load_config(result.config_path)
    runbook = open(result.runbook_path).read()

    assert config.write_mode == "cdc"
    assert config.cdc.keys == ["id"]
    assert config.cdc.sequence_by == "updated_at"
    assert "- Write Mode: `cdc`" in runbook


def test_authoring_workflow_generates_chosen_connector_and_credentials(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )

    assert workflow.connector_types() == [
        "bigquery",
        "databricks",
        "duckdb",
        "postgres",
        "sqlite",
    ]

    workflow.choose_connector("bigquery")
    workflow.set_connector_setting("project", "analytics-prod")
    workflow.set_connector_setting("dataset", "landing")
    workflow.acknowledge_confidence_tier("name")

    result = workflow.generate()
    config = load_config(result.config_path)
    runbook = open(result.runbook_path).read()

    assert config.connector.type == "bigquery"
    assert config.connector.options == {
        "project": "analytics-prod",
        "dataset": "landing",
    }
    assert "GOOGLE_APPLICATION_CREDENTIALS" in runbook
    assert "BigQuery Application Default Credentials" in runbook


def test_authoring_workflow_blocks_generation_when_connector_settings_missing(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.choose_connector("bigquery")
    workflow.set_connector_setting("project", "analytics-prod")
    workflow.acknowledge_confidence_tier("name")

    with pytest.raises(ValueError, match="dataset"):
        workflow.generate()


def test_authoring_workflow_never_exports_credential_values(tmp_path, monkeypatch):
    secret = "/tmp/do-not-export-service-account.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", secret)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.choose_connector("bigquery")
    workflow.set_connector_setting("project", "analytics-prod")
    workflow.set_connector_setting("dataset", "landing")
    workflow.acknowledge_confidence_tier("name")

    result = workflow.generate()

    assert secret not in open(result.config_path).read()
    assert secret not in open(result.runbook_path).read()
    assert secret not in open(result.registry_path).read()


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


def test_authoring_workflow_declares_field_encryption_on_destination_columns(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "ssn,name\n123-45-6789,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", type="string")
    workflow.set_field_encryption(
        "ssn",
        encrypt_key="env:SSN_ENC_KEY",
        hash_key="env:SSN_HASH_KEY",
    )
    _acknowledge_all(workflow)

    result = workflow.generate()
    config = load_config(result.config_path)
    runbook = open(result.runbook_path).read()

    ssn = next(c for c in config.columns if c.dest == "ssn")
    assert ssn.encrypt.algorithm == "aes-256-gcm"
    assert ssn.encrypt.key == "env:SSN_ENC_KEY"
    assert ssn.hash.algorithm == "hmac-sha256"
    assert ssn.hash.key == "env:SSN_HASH_KEY"
    assert "Source `ssn` -> destination `ssn`" in runbook
    assert "encrypt `aes-256-gcm`" in runbook
    assert "hash `hmac-sha256`" in runbook
    assert "env:SSN_ENC_KEY" in runbook


def test_authoring_workflow_supports_two_destinations_one_source(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "ssn,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", type="string")
    workflow.duplicate_column("ssn", new_dest="ssn_hash")
    workflow.set_field_encryption("ssn", encrypt_key="env:SSN_ENC_KEY")
    workflow.set_field_encryption("ssn_hash", hash_key="env:SSN_HASH_KEY")
    _acknowledge_all(workflow)

    result = workflow.generate()
    config = load_config(result.config_path)

    ssn_columns = [c for c in config.columns if c.source == "ssn"]
    assert {c.dest for c in ssn_columns} == {"ssn", "ssn_hash"}


def test_authoring_workflow_blocks_generation_on_field_encryption_shape_failure(
    tmp_path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    # encrypt is structurally only valid on type: string
    workflow.set_field_encryption("id", encrypt_key="env:K")
    _acknowledge_all(workflow)

    with pytest.raises(ValueError, match="type: string"):
        workflow.validate()


def test_authoring_workflow_never_leaks_field_encryption_key_material(
    tmp_path, monkeypatch
):
    secret = "super-secret-aes-256-key-material"
    monkeypatch.setenv("SSN_ENC_KEY", secret)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "ssn,name\n123,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", type="string")
    workflow.set_field_encryption("ssn", encrypt_key="env:SSN_ENC_KEY")
    _acknowledge_all(workflow)

    result = workflow.generate()

    for path in (result.config_path, result.runbook_path, result.registry_path):
        assert secret not in open(path).read()


def test_authoring_workflow_field_encryption_declarations_lists_columns(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "ssn,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", type="string")
    workflow.set_field_encryption(
        "ssn",
        encrypt_key="env:K",
        hash_key="env:H",
    )

    declarations = workflow.field_encryption_declarations()
    assert declarations == [
        {
            "source": "ssn",
            "dest": "ssn",
            "encrypt": {"algorithm": "aes-256-gcm", "key": "env:K"},
            "hash": {"algorithm": "hmac-sha256", "key": "env:H"},
        }
    ]


def test_authoring_workflow_field_encryption_placeholders_list_key_references(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "ssn,email\n1,a@example.com\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", type="string")
    workflow.draft.edit_column("email", type="string")
    workflow.set_field_encryption("ssn", encrypt_key="env:SSN_ENC_KEY")
    workflow.set_field_encryption("email", hash_key="env:EMAIL_HASH_KEY")

    placeholders = {p.env_var: p.purpose for p in workflow.credential_placeholders()}

    assert placeholders["SSN_ENC_KEY"] == (
        "Field Encryption encrypt key for destination ssn"
    )
    assert placeholders["EMAIL_HASH_KEY"] == (
        "Field Encryption hash key for destination email"
    )


def test_authoring_workflow_requires_draft_for_validation_and_generation(tmp_path):
    workflow = AuthoringWorkflow(
        file=str(tmp_path / "missing.txt"),
        workspace=str(tmp_path),
        dest_table="people",
        fmt="fixed_width",
    )

    assert workflow.suggested_commands() == []
    with pytest.raises(ValueError, match="Pipeline Config Draft"):
        workflow.validate()
    with pytest.raises(ValueError, match="Pipeline Config Draft"):
        workflow.generate()


def test_authoring_workflow_edit_column_invalidates_validation(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n2,Bob\n")
    workflow = AuthoringWorkflow.start(
        file=src, workspace=str(workspace), dest_table="people"
    )

    assert workflow.validate().ok
    assert workflow.validation_report is not None

    # Editing a column through the Workflow seam invalidates the cached report,
    # so a later generate() cannot ride a stale-green validation.
    workflow.edit_column("id", dest="identifier")
    assert workflow.validation_report is None
    assert workflow.draft.column_by_dest("identifier").source == "id"


def test_authoring_workflow_suggested_commands_match_runbook(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "id,name\n1,Alice\n")
    workflow = AuthoringWorkflow.start(
        file=src, workspace=str(workspace), dest_table="people"
    )
    workflow.validate()
    _acknowledge_all(workflow)
    result = workflow.generate()

    # One source of truth: every suggested command appears verbatim in the
    # Authoring Runbook, including the quoted Audit DB shell reference.
    runbook = open(result.runbook_path).read()
    commands = workflow.suggested_commands()
    assert commands  # non-empty after generation
    for command in commands:
        assert command in runbook
    assert any('--audit-db-url "$PEOPLE_AUDIT_DB_URL"' in cmd for cmd in commands)


# ---------------------------------------------------------------------------
# Re-author an existing Pipeline Folder (#173)
# ---------------------------------------------------------------------------


def _author_minimal_folder(tmp_path, body="id,name\n1,Alice\n2,Bob\n"):
    """Author one folder from scratch and return (workspace, sample, result)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", body)
    workflow = AuthoringWorkflow.start(
        file=src, workspace=str(workspace), dest_table="people"
    )
    workflow.validate()
    _acknowledge_all(workflow)
    result = workflow.generate()
    return str(workspace), src, result


def test_open_folder_seeds_draft_from_saved_config(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )

    saved = load_config(result.config_path)
    by_source = {c.source: c for c in reopened.draft.columns}
    assert set(by_source) == {c.source for c in saved.columns}
    assert by_source["id"].dest == "id"
    assert by_source["id"].type == "integer"


def test_open_folder_recovers_sample_file_from_runbook(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )

    assert reopened.file == src


def test_reauthor_save_back_overwrites_in_place(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    reopened.edit_column("id", type="string")
    reopened.validate()
    _acknowledge_all(reopened)
    reopened.generate()

    # Same Folder overwritten in place: the type change survived a reload.
    saved = load_config(result.config_path)
    by_source = {c.source: c for c in saved.columns}
    assert by_source["id"].type == "string"

    # Exactly one Folder and one Registry entry — no duplicate was created.
    pipelines_dir = os.path.join(workspace, "pipelines")
    assert os.listdir(pipelines_dir) == ["people"]
    registry = load_registry(workspace)
    assert [e.id for e in registry.entries] == ["people"]


def test_reauthor_save_back_changes_only_the_edited_column(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    before = load_config(result.config_path)

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    reopened.edit_column("id", type="string")
    reopened.validate()
    _acknowledge_all(reopened)
    reopened.generate()

    after = load_config(result.config_path)
    assert after.dest_table == before.dest_table
    assert after.format == before.format
    assert after.write_mode == before.write_mode
    assert after.connector.type == before.connector.type
    # Every column unchanged except `id`, whose type flipped to string.
    before_cols = {c.source: c for c in before.columns}
    after_cols = {c.source: c for c in after.columns}
    assert set(after_cols) == set(before_cols)
    for source in before_cols:
        expected_type = "string" if source == "id" else before_cols[source].type
        assert after_cols[source].type == expected_type
        assert after_cols[source].dest == before_cols[source].dest
        assert after_cols[source].required == before_cols[source].required


def test_reauthor_save_back_refreshes_runbook_timestamp(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    runbook_before = open(result.runbook_path).read()
    assert "Authored at: " in runbook_before

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    reopened.edit_column("id", type="string")
    reopened.validate()
    _acknowledge_all(reopened)
    reopened.generate()

    runbook_after = open(result.runbook_path).read()
    assert "Authored at: " in runbook_after
    # Both still name the same sample File.
    assert src in runbook_after


def test_reauthor_field_encryption_round_trips_and_regenerates_runbook(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(
        tmp_path,
        "people.csv",
        "id,ssn,email,phone\n1,123-45-6789,a@example.com,555-0000\n",
    )
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", dest="ssn_ciphertext", type="string")
    workflow.draft.edit_column("email", dest="email_token", type="string")
    workflow.draft.edit_column("phone", dest="phone_protected", type="string")
    workflow.set_field_encryption("ssn_ciphertext", encrypt_key="env:SSN_ENC_KEY")
    workflow.set_field_encryption("email_token", hash_key="env:EMAIL_HASH_KEY")
    workflow.set_field_encryption(
        "phone_protected",
        encrypt_key="env:PHONE_ENC_KEY",
        hash_key="env:PHONE_HASH_KEY",
    )
    _acknowledge_all(workflow)
    result = workflow.generate()
    before_yaml = yaml.safe_load(open(result.config_path).read())

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=str(workspace)
    )
    assert reopened.draft.column_by_dest("ssn_ciphertext").encrypt.key == (
        "env:SSN_ENC_KEY"
    )
    assert reopened.draft.column_by_dest("email_token").hash.key == (
        "env:EMAIL_HASH_KEY"
    )
    phone = reopened.draft.column_by_dest("phone_protected")
    assert phone.encrypt.key == "env:PHONE_ENC_KEY"
    assert phone.hash.key == "env:PHONE_HASH_KEY"

    reopened.validate()
    reopened.generate()

    after_yaml = yaml.safe_load(open(result.config_path).read())
    runbook = open(result.runbook_path).read()
    assert after_yaml == before_yaml
    for placeholder in (
        "SSN_ENC_KEY",
        "EMAIL_HASH_KEY",
        "PHONE_ENC_KEY",
        "PHONE_HASH_KEY",
    ):
        assert placeholder in runbook


def test_reauthor_field_encryption_never_writes_key_material(tmp_path, monkeypatch):
    secrets = {
        "SSN_ENC_KEY": "secret-ssn-key-material",
        "EMAIL_HASH_KEY": "secret-email-key-material",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = _file(tmp_path, "people.csv", "ssn,email\n123,a@example.com\n")
    workflow = AuthoringWorkflow.start(
        file=src,
        workspace=str(workspace),
        dest_table="people",
    )
    workflow.draft.edit_column("ssn", type="string")
    workflow.draft.edit_column("email", type="string")
    workflow.set_field_encryption("ssn", encrypt_key="env:SSN_ENC_KEY")
    workflow.set_field_encryption("email", hash_key="env:EMAIL_HASH_KEY")
    _acknowledge_all(workflow)
    result = workflow.generate()

    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=str(workspace)
    )
    reopened.edit_column("email", required=False)
    reopened.validate()
    reopened.generate()

    folder_abs = os.path.join(str(workspace), result.folder)
    for root, _, files in os.walk(folder_abs):
        for filename in files:
            text = open(os.path.join(root, filename)).read()
            for secret in secrets.values():
                assert secret not in text


def test_open_folder_rejects_unsupported_shape_clearly(tmp_path):
    # Hand-author a Folder with a non-CSV format the loader does not yet support.
    workspace = tmp_path / "ws"
    folder = workspace / "pipelines" / "events"
    folder.mkdir(parents=True)
    (folder / "pipeline.yaml").write_text(
        "format: csv\n"
        "dest_table: events\n"
        "write_mode: truncate\n"
        "connector:\n  type: sqlite\n"
        "columns:\n  - source: id\n    dest: id\n    type: integer\n"
    )
    (folder / "RUNBOOK.md").write_text(
        "## Sample File\n\nAuthored from sample File: `/data/events.csv`\n"
    )

    with pytest.raises(ValueError, match="truncate"):
        AuthoringWorkflow.open_folder(
            folder="pipelines/events", workspace=str(workspace)
        )


def _write_handauthored_folder(workspace, folder_rel, pipeline_yaml, sample_path):
    folder = workspace / folder_rel
    folder.mkdir(parents=True)
    (folder / "pipeline.yaml").write_text(pipeline_yaml)
    (folder / "RUNBOOK.md").write_text(
        f"## Sample File\n\nAuthored from sample File: `{sample_path}`\n"
    )
    registry_path = workspace / "pipeline-registry.yaml"
    registry_path.write_text("version: 1\npipelines: []\n")


def test_open_folder_loads_fixed_width_layout_without_schema_inference(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_handauthored_folder(
        workspace,
        "pipelines/people",
        "format: fixed_width\n"
        "dest_table: people\n"
        "write_mode: append\n"
        "connector:\n  type: sqlite\n"
        "columns:\n"
        "  - source: id\n    dest: id\n    type: integer\n    start: 1\n    width: 3\n"
        "  - source: name\n    dest: name\n    type: string\n    start: 4\n    width: 5\n",
        sample_path="/data/people.txt",
    )

    workflow = AuthoringWorkflow.open_folder(
        folder="pipelines/people", workspace=str(workspace)
    )
    assert workflow.fmt == "fixed_width"
    by_source = {c.source: c for c in workflow.draft.columns}
    assert by_source["id"].start == 1 and by_source["id"].width == 3
    assert by_source["name"].start == 4 and by_source["name"].width == 5
    # Loaded confidence sentinel means no Schema Inference tier evidence to show.
    assert all(c.confidence == "loaded" for c in workflow.draft.columns)
    # Re-author for fixed_width never runs Schema Inference, even on refresh.
    with pytest.raises(ValueError, match="fixed_width"):
        workflow.refresh_sample("/tmp/whatever.txt")


def test_open_folder_loads_excel_sheet_selector(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_handauthored_folder(
        workspace,
        "pipelines/orders",
        "format: excel\n"
        "dest_table: orders\n"
        "write_mode: append\n"
        "connector:\n  type: sqlite\n"
        "excel:\n  sheet: Orders\n"
        "columns:\n  - source: id\n    dest: id\n    type: integer\n",
        sample_path="/data/book.xlsx",
    )

    workflow = AuthoringWorkflow.open_folder(
        folder="pipelines/orders", workspace=str(workspace)
    )
    assert workflow.fmt == "excel"
    assert workflow.sheet == "Orders"
    assert workflow.draft.sheet == "Orders"
    # Sheet is editable through the draft; the new value rides into to_config_dict.
    workflow.draft.sheet = "Returns"
    assert workflow.draft.to_config_dict()["excel"] == {"sheet": "Returns"}


# ---------------------------------------------------------------------------
# Fresh sample + Confidence Tier refresh on re-author (#174)
# ---------------------------------------------------------------------------


def test_refresh_sample_fills_confidence_tier_on_loaded_columns(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    # Loaded columns start with the "loaded" sentinel — no inference evidence yet.
    assert all(c.confidence == "loaded" for c in reopened.draft.columns)

    fresh = _file(tmp_path, "fresh.csv", "id,name\n1,Alice\n2,Bob\n3,Carol\n")
    reopened.refresh_sample(fresh)

    by_source = {c.source: c for c in reopened.draft.columns}
    # The "loaded" sentinel is replaced by a real inference tier from the sample.
    assert by_source["id"].confidence == "high"
    assert by_source["name"].confidence != "loaded"
    assert by_source["id"].total_seen == 3


def test_refresh_sample_never_overwrites_authored_fields(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    # Author chose: id is a string, renamed to identifier, optional.
    reopened.edit_column("id", dest="identifier", type="string", required=False)

    # A fresh sample where id looks like an integer must NOT flip the type back.
    fresh = _file(tmp_path, "fresh.csv", "id,name\n10,Alice\n20,Bob\n")
    reopened.refresh_sample(fresh)

    id_col = reopened.draft.column("id")
    assert id_col.type == "string"       # authored field preserved
    assert id_col.dest == "identifier"   # authored field preserved
    assert id_col.required is False      # authored field preserved
    assert id_col.confidence != "loaded" # evidence still refreshed


def test_refresh_sample_can_downgrade_a_tier_when_new_data_has_nulls(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    # A fresh sample where `id` has a null row -> integer tier drops to low.
    fresh = _file(tmp_path, "fresh.csv", "id,name\n1,Alice\n,Bob\n3,Carol\n")
    reopened.refresh_sample(fresh)

    id_col = reopened.draft.column("id")
    assert id_col.confidence == "low"
    assert id_col.null_count == 1


def test_refresh_sample_records_new_sample_in_regenerated_runbook(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    fresh = _file(tmp_path, "fresh.csv", "id,name\n1,Alice\n2,Bob\n")
    reopened.refresh_sample(fresh)
    reopened.validate()
    _acknowledge_all(reopened)
    reopened.generate()

    runbook = open(result.runbook_path).read()
    assert fresh in runbook
    assert src not in runbook


def test_default_sample_file_uses_runbook_sample_when_present_else_none(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    # The original sample still exists on disk.
    assert reopened.default_sample_file() == src

    # If the recorded sample is gone, the picker has no default.
    os.remove(src)
    assert reopened.default_sample_file() is None


def test_refresh_sample_invalidates_prior_validation(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    reopened.validate()
    assert reopened.validation_report is not None

    reopened.refresh_sample(_file(tmp_path, "fresh.csv", "id,name\n1,Alice\n"))
    # A fresh sample is new evidence; the prior validation no longer applies.
    assert reopened.validation_report is None


def test_refresh_sample_leaves_columns_absent_from_new_sample_untouched(tmp_path):
    workspace, src, result = _author_minimal_folder(tmp_path)
    reopened = AuthoringWorkflow.open_folder(
        folder=result.folder, workspace=workspace
    )
    # The fresh sample drops the `name` column entirely.
    fresh = _file(tmp_path, "fresh.csv", "id\n1\n2\n")
    reopened.refresh_sample(fresh)

    # `id` got fresh evidence; `name` (absent from the sample) keeps the sentinel.
    assert reopened.draft.column("id").confidence == "high"
    assert reopened.draft.column("name").confidence == "loaded"
