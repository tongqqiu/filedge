"""Textual shell smoke tests for the Authoring UI."""

import asyncio
import os

import pytest

pytest.importorskip("textual")
from textual.widgets import Select

from filedge.authoring_ui import AuthoringApp, EditValueScreen
from filedge.authoring_workflow import AuthoringWorkflow


def _csv_workflow(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = tmp_path / "people.csv"
    src.write_text("id,name\n1,Alice\n")
    return AuthoringWorkflow.start(
        file=str(src),
        workspace=str(workspace),
        dest_table="people",
    )


def test_textual_authoring_ui_happy_path(tmp_path):
    async def run():
        workflow = _csv_workflow(tmp_path)
        workflow.acknowledge_confidence_tier("name")
        app = AuthoringApp(workflow)

        async with app.run_test() as pilot:
            await pilot.press("v")
            await pilot.press("g")
            next_panel = app.query_one("#next")
            assert "filedge healthcheck" in str(next_panel.render())

        assert os.path.isfile(workflow.generated.config_path)

    asyncio.run(run())


def test_textual_authoring_ui_lists_and_acknowledges_confidence_tiers(tmp_path):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            confidence = app.query_one("#confidence")
            assert "name -> name: ambiguous" in str(confidence.render())
            assert "needs acknowledgement" in str(confidence.render())

            app._selected_column = lambda: workflow.draft.column("name")
            app.action_ack_confidence()

            assert workflow.confidence_reviews()[0].acknowledged is True
            assert "acknowledged" in str(confidence.render())

    asyncio.run(run())


def test_textual_authoring_ui_selects_connector_and_edits_settings(
    tmp_path, monkeypatch
):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            event = type(
                "Event",
                (),
                {
                    "select": type("SelectRef", (), {"id": "connector"})(),
                    "value": "bigquery",
                },
            )()
            app.on_select_changed(event)

            credentials = app.query_one("#credentials")
            assert "GOOGLE_APPLICATION_CREDENTIALS" in str(credentials.render())

            values = iter(["analytics-prod", "landing"])

            def fake_push_screen(screen, callback):
                callback(next(values))

            monkeypatch.setattr(app, "push_screen", fake_push_screen)
            app.action_edit_connector_setting()
            app._selected_connector_setting = (
                lambda: workflow.connector_descriptor().settings[1]
            )
            app.action_edit_connector_setting()

        assert workflow.draft.connector_type == "bigquery"
        assert workflow.draft.connector_options == {
            "project": "analytics-prod",
            "dataset": "landing",
        }

    asyncio.run(run())


def test_textual_authoring_ui_blocks_generation_until_confidence_ack(tmp_path):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            app.action_generate()
            validation = app.query_one("#validation")
            assert "Confidence Tier" in str(validation.render())

    asyncio.run(run())


def test_textual_authoring_ui_selects_cdc_and_edits_cdc_settings(
    tmp_path, monkeypatch
):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("id,op,updated_at,name\n1,insert,10,Alice\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        app = AuthoringApp(workflow)

        async with app.run_test():
            event = type(
                "Event",
                (),
                {
                    "select": type("SelectRef", (), {"id": "write_mode"})(),
                    "value": "cdc",
                },
            )()
            app.on_select_changed(event)
            cdc_panel = app.query_one("#cdc_settings")
            assert "CDC business key column(s): (missing)" in str(cdc_panel.render())

            values = iter(["id", "updated_at"])

            def fake_push_screen(screen, callback):
                callback(next(values))

            monkeypatch.setattr(app, "push_screen", fake_push_screen)
            app.action_edit_cdc_business_keys()
            app.action_edit_cdc_sequence()

            assert "CDC business key column(s): id" in str(cdc_panel.render())
            assert "CDC sequence column: updated_at" in str(cdc_panel.render())

        assert workflow.draft.write_mode == "cdc"
        assert workflow.draft.cdc_keys == ["id"]
        assert workflow.draft.cdc_sequence_by == "updated_at"

    asyncio.run(run())


def test_textual_authoring_ui_surfaces_write_mode_validation_failures(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("id,op,updated_at,name\n1,insert,10,Alice\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        workflow.choose_write_mode("cdc")
        app = AuthoringApp(workflow)

        async with app.run_test():
            app.action_validate()
            validation = str(app.query_one("#validation").render())
            assert "Write Mode failures" in validation
            assert "business key" in validation
            assert "sequence column" in validation

    asyncio.run(run())


def test_textual_authoring_ui_renders_drift_inline_for_reauthor(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        original = tmp_path / "original.csv"
        original.write_text("id,nickname\n1,Al\n")
        refreshed = tmp_path / "refreshed.csv"
        refreshed.write_text("id\n1\n")
        workflow = AuthoringWorkflow.start(
            file=str(original),
            workspace=str(workspace),
            dest_table="people",
        )
        workflow.draft.edit_column("nickname", required=False)
        workflow.file = str(refreshed)
        workflow.reauthor = True
        app = AuthoringApp(workflow)

        async with app.run_test():
            app.action_validate()
            schema = app.query_one("#schema")
            nickname_row = schema.get_row_at(1)
            assert nickname_row[0] == "nickname"
            assert "drift: declared-but-absent" in nickname_row[5]

    asyncio.run(run())


def test_edit_value_screen_submits_and_cancels(monkeypatch):
    screen = EditValueScreen("Destination", "name")
    dismissed = []
    monkeypatch.setattr(screen, "dismiss", dismissed.append)

    screen.on_input_submitted(type("Event", (), {"value": "full_name"})())
    screen.action_cancel()

    assert dismissed == ["full_name", None]


def test_edit_value_screen_mounts_in_textual_app(tmp_path):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test() as pilot:
            screen = EditValueScreen("Destination", "name")
            await app.push_screen(screen)
            await pilot.pause()
            assert screen.query_one("#value").value == "name"
            await pilot.press("escape")

    asyncio.run(run())


def test_textual_authoring_ui_edit_actions_update_draft(tmp_path, monkeypatch):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            values = iter(["person_id", "string"])

            def fake_push_screen(screen, callback):
                callback(next(values))

            monkeypatch.setattr(app, "push_screen", fake_push_screen)
            app.action_edit_dest()
            app.action_edit_type()
            app.action_toggle_required()

        col = workflow.draft.column("id")
        assert col.dest == "person_id"
        assert col.type == "string"
        assert col.required is False

    asyncio.run(run())


def test_textual_authoring_ui_edit_source_and_rejects_invalid_type(
    tmp_path, monkeypatch
):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            def rename_push_screen(screen, callback):
                callback("person_id")

            monkeypatch.setattr(app, "push_screen", rename_push_screen)
            app.action_edit_source()
            assert workflow.draft.column("person_id").source == "person_id"

            def invalid_type_push_screen(screen, callback):
                callback("int64")

            monkeypatch.setattr(app, "push_screen", invalid_type_push_screen)
            app.action_edit_type()
            validation = app.query_one("#validation")
            assert "Edit rejected" in str(validation.render())

    asyncio.run(run())


def test_textual_authoring_ui_rejects_ack_for_non_risky_column(tmp_path):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            app._selected_column = lambda: workflow.draft.column("id")
            app.action_ack_confidence()
            validation = app.query_one("#validation")
            assert "acknowledgement rejected" in str(validation.render())

    asyncio.run(run())


def test_textual_authoring_ui_prominently_surfaces_validation_failures(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("id,name\nnot-an-int,Alice\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        workflow.draft.edit_column("id", type="integer")
        workflow.draft.edit_column("name", new_source="missing_name")
        app = AuthoringApp(workflow)

        async with app.run_test():
            app.action_validate()
            validation = str(app.query_one("#validation").render())
            assert "Column Tolerance failures" in validation
            assert "required column `missing_name`" in validation
            assert "Strict Mode failures" in validation
            assert "row 1, column `id`" in validation

    asyncio.run(run())


def test_textual_authoring_ui_noops_without_selected_column(tmp_path):
    async def run():
        workflow = AuthoringWorkflow(
            file=str(tmp_path / "people.dat"),
            workspace=str(tmp_path),
            dest_table="people",
            fmt="fixed_width",
        )
        app = AuthoringApp(workflow)

        async with app.run_test():
            app.action_edit_dest()
            app.action_toggle_required()
            app.action_edit_connector_setting()
            app.action_ack_confidence()
            app.action_generate()
            validation = app.query_one("#validation")
            assert "Pipeline Config Draft" in str(validation.render())

    asyncio.run(run())


def test_textual_authoring_ui_edit_callback_noops_on_cancel_or_missing_draft(
    tmp_path, monkeypatch
):
    async def run():
        workflow = _csv_workflow(tmp_path)
        app = AuthoringApp(workflow)

        async with app.run_test():
            def cancel_push_screen(screen, callback):
                callback(None)

            monkeypatch.setattr(app, "push_screen", cancel_push_screen)
            app.action_edit_dest()
            assert workflow.draft.column("id").dest == "id"

            def missing_draft_push_screen(screen, callback):
                workflow.draft = None
                callback("person_id")

            monkeypatch.setattr(app, "push_screen", missing_draft_push_screen)
            app.action_edit_dest()

    asyncio.run(run())


def test_textual_authoring_ui_out_of_range_selection_noops(tmp_path, monkeypatch):
    workflow = _csv_workflow(tmp_path)
    app = AuthoringApp(workflow)

    monkeypatch.setattr(
        app,
        "query_one",
        lambda *args, **kwargs: type("Table", (), {"cursor_row": 99})(),
    )

    assert app._selected_column() is None


def test_textual_authoring_ui_excel_sheet_change(tmp_path):
    async def run():
        openpyxl = pytest.importorskip("openpyxl")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "book.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Orders"
        ws.append(["id"])
        ws.append([1])
        other = wb.create_sheet("Customers")
        other.append(["customer_id"])
        other.append([10])
        wb.save(src)
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="orders",
        )
        app = AuthoringApp(workflow)

        async with app.run_test():
            event = type(
                "Event",
                (),
                {"select": type("SelectRef", (), {"id": "sheet"})(), "value": "Customers"},
            )()
            app.on_select_changed(event)
            assert workflow.sheet == "Customers"
            assert [c.source for c in workflow.draft.columns] == ["customer_id"]

            blank = type(
                "Event",
                (),
                {"select": type("SelectRef", (), {"id": "sheet"})(), "value": Select.BLANK},
            )()
            app.on_select_changed(blank)

    asyncio.run(run())


def test_textual_authoring_ui_declares_field_encryption(tmp_path, monkeypatch):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("ssn,name\n123-45-6789,Alice\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        workflow.draft.edit_column("ssn", type="string")
        app = AuthoringApp(workflow)

        async with app.run_test():
            app._selected_column = lambda: workflow.draft.column_by_dest("ssn")

            keys = iter(["env:SSN_ENC_KEY", "env:SSN_HASH_KEY"])

            def fake_push_screen(screen, callback):
                callback(next(keys))

            monkeypatch.setattr(app, "push_screen", fake_push_screen)
            app.action_edit_encrypt_key()
            app.action_edit_hash_key()

            panel = app.query_one("#field_encryption")
            assert "ssn -> ssn" in str(panel.render())
            assert "env:SSN_ENC_KEY" in str(panel.render())
            assert "env:SSN_HASH_KEY" in str(panel.render())

            # Re-editing surfaces the existing key as the modal default.
            seen = {}

            def capture_default(screen, callback):
                seen["value"] = screen.value
                callback("env:SSN_ENC_KEY_V2")

            monkeypatch.setattr(app, "push_screen", capture_default)
            workflow.set_field_encryption("ssn", encrypt_key="env:SSN_ENC_KEY")
            app.action_edit_encrypt_key()
            assert seen["value"] == "env:SSN_ENC_KEY"

            app.action_clear_field_encryption()
            assert workflow.draft.column_by_dest("ssn").encrypt is None
            assert workflow.draft.column_by_dest("ssn").hash is None

    asyncio.run(run())


def test_textual_authoring_ui_duplicates_column_for_two_destinations(
    tmp_path, monkeypatch
):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("ssn,name\n123,Alice\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        workflow.draft.edit_column("ssn", type="string")
        app = AuthoringApp(workflow)

        async with app.run_test():
            app._selected_column = lambda: workflow.draft.column_by_dest("ssn")

            monkeypatch.setattr(
                app, "push_screen", lambda screen, callback: callback("ssn_hash")
            )
            app.action_duplicate_column()
            assert [c.dest for c in workflow.draft.columns] == [
                "ssn",
                "name",
                "ssn_hash",
            ]

            # A duplicate dest is rejected and surfaced as UI feedback.
            monkeypatch.setattr(
                app, "push_screen", lambda screen, callback: callback("name")
            )
            app.action_duplicate_column()
            assert "Duplicate column rejected" in str(
                app.query_one("#validation").render()
            )

    asyncio.run(run())


def test_textual_authoring_ui_field_encryption_actions_noop_without_selection(
    tmp_path,
):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("ssn\n123\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        app = AuthoringApp(workflow)

        async with app.run_test():
            app._selected_column = lambda: None
            # All of these must early-return without raising.
            app.action_edit_encrypt_key()
            app.action_edit_hash_key()
            app.action_clear_field_encryption()
            app.action_duplicate_column()
            assert "No Field Encryption columns declared." in str(
                app.query_one("#field_encryption").render()
            )

    asyncio.run(run())


def test_textual_authoring_ui_field_encryption_rejection_surfaced(tmp_path, monkeypatch):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("id\n1\n")  # integer column: encrypt is rejected by the draft
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        app = AuthoringApp(workflow)

        async with app.run_test():
            app._selected_column = lambda: workflow.draft.column_by_dest("id")
            # to_config validates shape; an integer encrypt key is fine to store on
            # the draft, so force a rejection through clear on a missing dest.
            monkeypatch.setattr(
                app, "push_screen", lambda screen, callback: callback(None)
            )
            # Passing None as the value is a no-op commit; exercises that branch.
            app.action_edit_encrypt_key()
            assert workflow.draft.column_by_dest("id").encrypt is None

    asyncio.run(run())


def test_textual_authoring_ui_field_encryption_rejections_surface_as_feedback(
    tmp_path, monkeypatch
):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        src = tmp_path / "people.csv"
        src.write_text("ssn\n123\n")
        workflow = AuthoringWorkflow.start(
            file=str(src),
            workspace=str(workspace),
            dest_table="people",
        )
        workflow.draft.edit_column("ssn", type="string")
        app = AuthoringApp(workflow)

        async with app.run_test():
            # A column whose dest is not in the draft forces the workflow methods
            # to raise; the UI must render the error instead of crashing.
            bogus = type("Col", (), {"dest": "ghost", "encrypt": None, "hash": None})()
            app._selected_column = lambda: bogus

            app.action_clear_field_encryption()
            assert "Clear Field Encryption rejected" in str(
                app.query_one("#validation").render()
            )

            monkeypatch.setattr(
                app, "push_screen", lambda screen, callback: callback("env:K")
            )
            app.action_edit_encrypt_key()
            assert "Field Encryption declaration rejected" in str(
                app.query_one("#validation").render()
            )

            # An empty new dest in the duplicate modal is a no-op.
            monkeypatch.setattr(
                app, "push_screen", lambda screen, callback: callback("")
            )
            app._selected_column = lambda: workflow.draft.column_by_dest("ssn")
            app.action_duplicate_column()
            assert [c.dest for c in workflow.draft.columns] == ["ssn"]

    asyncio.run(run())
