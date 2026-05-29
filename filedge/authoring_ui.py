"""Textual shell for the Authoring UI."""

from filedge.authoring_validation import (
    SCOPE_COLUMN_TOLERANCE,
    SCOPE_STRICT_MODE,
    SCOPE_WRITE_MODE,
)
from filedge.authoring_workflow import AuthoringWorkflow

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import DataTable, Footer, Header, Select, Static
    from textual.widgets import Input
except ImportError as exc:  # pragma: no cover - exercised through CLI fallback
    raise ImportError(
        "The Authoring UI requires the optional authoring extra: "
        "pip install filedge[authoring]"
    ) from exc


class EditValueScreen(ModalScreen[str | None]):
    """Small modal used to edit one field on the focused schema row."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, label: str, value: str):
        super().__init__()
        self.label = label
        self.value = value

    def compose(self) -> ComposeResult:
        yield Static(self.label)
        yield Input(value=self.value, id="value")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AuthoringApp(App):
    """A thin Textual UI over the headless Authoring Workflow."""

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #schema { width: 2fr; }
    #side { width: 1fr; }
    Static { padding: 0 1; }
    """

    BINDINGS = [
        Binding("s", "edit_source", "Source", priority=True),
        Binding("d", "edit_dest", "Dest", priority=True),
        Binding("t", "edit_type", "Type", priority=True),
        Binding("r", "toggle_required", "Required", priority=True),
        Binding("b", "edit_cdc_business_keys", "Business Key", priority=True),
        Binding("e", "edit_cdc_sequence", "Sequence", priority=True),
        Binding("o", "edit_connector_setting", "Connector Setting", priority=True),
        Binding("E", "edit_encrypt_key", "Encrypt Key", priority=True),
        Binding("H", "edit_hash_key", "Hash Key", priority=True),
        Binding("X", "clear_field_encryption", "Clear Encryption", priority=True),
        Binding("D", "duplicate_column", "Duplicate Column", priority=True),
        Binding("a", "ack_confidence", "Acknowledge", priority=True),
        Binding("v", "validate", "Validate", priority=True),
        Binding("g", "generate", "Generate", priority=True),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, workflow: AuthoringWorkflow):
        super().__init__()
        self.workflow = workflow

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            yield DataTable(id="schema")
            with Vertical(id="side"):
                yield Static(self._summary(), id="summary")
                yield Select(
                    [(mode, mode) for mode in self.workflow.write_modes()],
                    value=self.workflow.draft.write_mode
                    if self.workflow.draft is not None
                    else "append",
                    id="write_mode",
                )
                yield Static("", id="cdc_settings")
                yield Select(
                    [(name, name) for name in self.workflow.connector_types()],
                    value=self.workflow.draft.connector_type
                    if self.workflow.draft is not None
                    else "sqlite",
                    id="connector",
                )
                yield DataTable(id="connector_settings")
                yield Static("", id="credentials")
                yield Static("", id="field_encryption")
                yield Static("", id="confidence")
                if self.workflow.fmt == "excel":
                    yield Select(
                        [(name, name) for name in self.workflow.excel_sheets],
                        value=self.workflow.sheet,
                        id="sheet",
                    )
                yield DataTable(id="preview")
                yield Static("Authoring Validation has not run.", id="validation")
                yield Static("", id="next")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_schema()
        self._populate_cdc_settings()
        self._populate_connector()
        self._populate_confidence()
        self._populate_field_encryption()
        self._populate_preview()
        self.query_one("#schema", DataTable).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sheet" and event.value is not Select.BLANK:
            self.workflow.choose_sheet(event.value)
            self.query_one("#summary", Static).update(self._summary())
            self._populate_schema()
            self._populate_cdc_settings()
            self._populate_connector()
            self._populate_confidence()
            self._populate_preview()
        elif event.select.id == "write_mode" and event.value is not Select.BLANK:
            try:
                self.workflow.choose_write_mode(str(event.value))
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(
                    f"Write Mode selection rejected: {e}"
                )
                return
            self._populate_cdc_settings()
        elif event.select.id == "connector" and event.value is not Select.BLANK:
            try:
                self.workflow.choose_connector(str(event.value))
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(
                    f"Connector selection rejected: {e}"
                )
                return
            self._populate_connector()

    def action_validate(self) -> None:
        report = self.workflow.validate()
        status = "green" if report.ok else "red"
        lines = [f"Authoring Validation: {status} ({report.rows_checked} row(s))"]
        column_tolerance = [
            f for f in report.findings_in(SCOPE_COLUMN_TOLERANCE) if not f.ok
        ]
        strict_mode = [f for f in report.findings_in(SCOPE_STRICT_MODE) if not f.ok]
        write_mode = [f for f in report.findings_in(SCOPE_WRITE_MODE) if not f.ok]
        if write_mode:
            lines.append("")
            lines.append("Write Mode failures")
            lines.extend(f"- {f.message}" for f in write_mode)
        if column_tolerance:
            lines.append("")
            lines.append("Column Tolerance failures")
            lines.extend(
                f"- required column `{f.column}`: {f.message}"
                for f in column_tolerance
            )
        if strict_mode:
            lines.append("")
            lines.append("Strict Mode failures")
            lines.extend(
                (
                    f"- row {f.row_number}, column `{f.column}`: {f.message}"
                    if f.row_number is not None
                    else f"- column `{f.column}`: {f.message}"
                )
                for f in strict_mode
            )
        lines.append("")
        lines.append("Validation Scope findings")
        lines.extend(f"- {f.scope}: {f.message}" for f in report.findings)
        self.query_one("#validation", Static).update("\n".join(lines))

    def action_edit_source(self) -> None:
        self._edit_field("source", "Source")

    def action_edit_dest(self) -> None:
        self._edit_field("dest", "Destination")

    def action_edit_type(self) -> None:
        self._edit_field("type", "Column Type")

    def action_toggle_required(self) -> None:
        column = self._selected_column()
        if column is None or self.workflow.draft is None:
            return
        self.workflow.edit_column(column.source, required=not column.required)
        self._populate_schema()
        self._populate_confidence()

    def action_edit_cdc_business_keys(self) -> None:
        if self.workflow.draft is None or self.workflow.draft.write_mode != "cdc":
            return

        def commit(value: str | None) -> None:
            if value is None:
                return
            keys = [part.strip() for part in value.split(",")]
            self.workflow.set_cdc_settings(business_keys=keys)
            self._populate_cdc_settings()

        value = ", ".join(self.workflow.draft.cdc_keys)
        return self.push_screen(
            EditValueScreen("CDC business key column(s)", value), commit
        )

    def action_edit_cdc_sequence(self) -> None:
        if self.workflow.draft is None or self.workflow.draft.write_mode != "cdc":
            return

        def commit(value: str | None) -> None:
            if value is None:
                return
            self.workflow.set_cdc_settings(sequence_by=value)
            self._populate_cdc_settings()

        return self.push_screen(
            EditValueScreen("CDC sequence column", self.workflow.draft.cdc_sequence_by),
            commit,
        )

    def action_edit_connector_setting(self) -> None:
        setting = self._selected_connector_setting()
        if setting is None or self.workflow.draft is None:
            return
        value = self.workflow.draft.connector_options.get(setting.name, "")

        def commit(new_value: str | None) -> None:
            if new_value is None:
                return
            try:
                self.workflow.set_connector_setting(setting.name, new_value)
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(
                    f"Connector setting rejected: {e}"
                )
                return
            self._populate_connector()

        return self.push_screen(EditValueScreen(setting.name, value), commit)

    def action_ack_confidence(self) -> None:
        column = self._selected_column()
        if column is None:
            return
        try:
            self.workflow.acknowledge_confidence_tier(column.source)
        except Exception as e:  # noqa: BLE001 - rendered as UI feedback
            self.query_one("#validation", Static).update(
                f"Confidence Tier acknowledgement rejected: {e}"
            )
            return
        self._populate_confidence()

    def action_edit_encrypt_key(self) -> None:
        self._edit_field_encryption_key("encrypt")

    def action_edit_hash_key(self) -> None:
        self._edit_field_encryption_key("hash")

    def action_clear_field_encryption(self) -> None:
        column = self._selected_column()
        if column is None:
            return
        try:
            self.workflow.clear_field_encryption(
                column.dest, encrypt=True, hash=True
            )
        except Exception as e:  # noqa: BLE001 - rendered as UI feedback
            self.query_one("#validation", Static).update(
                f"Clear Field Encryption rejected: {e}"
            )
            return
        self._populate_schema()
        self._populate_field_encryption()

    def action_duplicate_column(self) -> None:
        column = self._selected_column()
        if column is None:
            return

        def commit(value: str | None) -> None:
            if not value:
                return
            try:
                self.workflow.duplicate_column(column.dest, new_dest=value)
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(
                    f"Duplicate column rejected: {e}"
                )
                return
            self._populate_schema()
            self._populate_field_encryption()

        return self.push_screen(
            EditValueScreen(
                f"New destination column name (clones `{column.dest}`)",
                f"{column.dest}_copy",
            ),
            commit,
        )

    def _edit_field_encryption_key(self, kind: str) -> None:
        column = self._selected_column()
        if column is None:
            return
        current = (
            (column.encrypt.key if column.encrypt else "")
            if kind == "encrypt"
            else (column.hash.key if column.hash else "")
        )
        label = (
            "Field Encryption key reference (env:NAME or secrets:/abs/path)\n"
            "Key material is never collected by the Authoring UI."
        )

        def commit(value: str | None) -> None:
            if value is None:
                return
            kwargs = {"encrypt_key": value} if kind == "encrypt" else {"hash_key": value}
            try:
                self.workflow.set_field_encryption(column.dest, **kwargs)
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(
                    f"Field Encryption declaration rejected: {e}"
                )
                return
            self._populate_schema()
            self._populate_field_encryption()

        return self.push_screen(EditValueScreen(label, current), commit)

    def action_generate(self) -> None:
        try:
            result = self.workflow.generate()
        except Exception as e:  # noqa: BLE001 - rendered as UI feedback
            self.query_one("#validation", Static).update(f"Artifact generation blocked: {e}")
            return
        lines = [
            f"Pipeline Folder written: {result.folder}",
            "Destination reachability belongs to filedge healthcheck.",
            "",
            "Suggested next commands:",
            *self.workflow.suggested_commands(),
        ]
        self.query_one("#next", Static).update("\n".join(lines))

    def _edit_field(self, field: str, label: str) -> None:
        column = self._selected_column()
        if column is None:
            return

        def commit(value: str | None) -> None:
            if value is None:
                return
            try:
                if self.workflow.draft is None:
                    return
                if field == "source":
                    self.workflow.edit_column(column.source, new_source=value)
                elif field == "dest":
                    self.workflow.edit_column(column.source, dest=value)
                elif field == "type":
                    self.workflow.edit_column(column.source, type=value)
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(f"Edit rejected: {e}")
                return
            self._populate_schema()
            self._populate_confidence()

        return self.push_screen(EditValueScreen(label, str(getattr(column, field))), commit)

    def _selected_column(self):
        if self.workflow.draft is None:
            return None
        table = self.query_one("#schema", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if row < 0 or row >= len(self.workflow.draft.columns):
            return None
        return self.workflow.draft.columns[row]

    def _selected_connector_setting(self):
        if self.workflow.draft is None:
            return None
        settings = self.workflow.connector_descriptor().settings
        table = self.query_one("#connector_settings", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if row < 0 or row >= len(settings):
            return None
        return settings[row]

    def _summary(self) -> str:
        return (
            "Authoring Workflow\n"
            f"Sample File: {self.workflow.file}\n"
            f"Format: {self.workflow.fmt}\n"
            f"Destination: {self.workflow.dest_table}"
        )

    def _populate_cdc_settings(self) -> None:
        panel = self.query_one("#cdc_settings", Static)
        draft = self.workflow.draft
        if draft is None:
            panel.update("Write Mode: manual")
            return
        if draft.write_mode != "cdc":
            panel.update(f"Write Mode: {draft.write_mode}")
            return
        keys = ", ".join(draft.cdc_keys) if draft.cdc_keys else "(missing)"
        sequence = draft.cdc_sequence_by or "(missing)"
        panel.update(
            "Write Mode: cdc\n"
            f"CDC business key column(s): {keys}\n"
            f"CDC sequence column: {sequence}"
        )

    def _populate_schema(self) -> None:
        table = self.query_one("#schema", DataTable)
        table.clear(columns=True)
        table.add_columns(
            "source", "dest", "Column Type", "required", "Confidence Tier", "notes"
        )
        if self.workflow.draft is None:
            table.add_row(
                "Fixed-Width Layout",
                "manual",
                "manual",
                "manual",
                "manual",
                "Enter source/dest/type/start/width in the fixed-width layout surface.",
            )
            return
        for col in self.workflow.draft.columns:
            table.add_row(
                col.source,
                col.dest,
                col.type,
                "yes" if col.required else "no",
                col.confidence,
                "; ".join(col.notes),
            )

    def _populate_connector(self) -> None:
        settings = self.query_one("#connector_settings", DataTable)
        settings.clear(columns=True)
        settings.add_columns("setting", "required", "value")
        credentials = self.query_one("#credentials", Static)
        if self.workflow.draft is None:
            settings.add_row("Connector", "manual", "Create a Pipeline Config Draft.")
            credentials.update("No Credential Placeholders.")
            return
        descriptor = self.workflow.connector_descriptor()
        if descriptor.settings:
            for setting in descriptor.settings:
                settings.add_row(
                    setting.name,
                    "yes" if setting.required else "no",
                    self.workflow.draft.connector_options.get(setting.name, ""),
                )
        else:
            settings.add_row("No required non-secret settings.", "", "")
        placeholders = descriptor.credential_placeholders
        if not placeholders:
            credentials.update("No Connector Credential Placeholders.")
            return
        credentials.update(
            "Credential Placeholders\n"
            + "\n".join(f"- {p.env_var}: {p.purpose}" for p in placeholders)
        )

    def _populate_confidence(self) -> None:
        reviews = self.workflow.confidence_reviews()
        if not reviews:
            self.query_one("#confidence", Static).update(
                "No low or ambiguous Confidence Tier columns."
            )
            return
        lines = ["Confidence Tier review"]
        for review in reviews:
            state = "acknowledged" if review.acknowledged else "needs acknowledgement"
            lines.append(
                f"- {review.source} -> {review.dest}: {review.confidence} "
                f"({state}); {review.evidence}"
            )
        self.query_one("#confidence", Static).update("\n".join(lines))

    def _populate_field_encryption(self) -> None:
        panel = self.query_one("#field_encryption", Static)
        declarations = self.workflow.field_encryption_declarations()
        if not declarations:
            panel.update("No Field Encryption columns declared.")
            return
        lines = ["Field Encryption (key material not collected)"]
        for item in declarations:
            parts = [f"- {item['source']} -> {item['dest']}"]
            if item.get("encrypt"):
                parts.append(
                    f"encrypt {item['encrypt']['algorithm']} "
                    f"key={item['encrypt']['key']}"
                )
            if item.get("hash"):
                parts.append(
                    f"hash {item['hash']['algorithm']} "
                    f"key={item['hash']['key']}"
                )
            lines.append("; ".join(parts))
        panel.update("\n".join(lines))

    def _populate_preview(self) -> None:
        table = self.query_one("#preview", DataTable)
        table.clear(columns=True)
        rows = self.workflow.preview_rows
        if not rows:
            table.add_column("preview")
            table.add_row("No preview rows yet.")
            return
        columns = list(rows[0])
        table.add_columns(*columns)
        for row in rows:
            table.add_row(*(str(row.get(c, "")) for c in columns))
