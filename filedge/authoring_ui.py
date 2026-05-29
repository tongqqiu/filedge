"""Textual shell for the Authoring UI."""

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
        self._populate_preview()
        self.query_one("#schema", DataTable).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sheet" and event.value is not Select.BLANK:
            self.workflow.choose_sheet(event.value)
            self.query_one("#summary", Static).update(self._summary())
            self._populate_schema()
            self._populate_preview()

    def action_validate(self) -> None:
        report = self.workflow.validate()
        status = "green" if report.ok else "red"
        lines = [f"Authoring Validation: {status} ({report.rows_checked} row(s))"]
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
        self.workflow.draft.edit_column(column.source, required=not column.required)
        self._populate_schema()

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
                    self.workflow.draft.edit_column(column.source, new_source=value)
                elif field == "dest":
                    self.workflow.draft.edit_column(column.source, dest=value)
                elif field == "type":
                    self.workflow.draft.edit_column(column.source, type=value)
            except Exception as e:  # noqa: BLE001 - rendered as UI feedback
                self.query_one("#validation", Static).update(f"Edit rejected: {e}")
                return
            self._populate_schema()

        return self.push_screen(EditValueScreen(label, str(getattr(column, field))), commit)

    def _selected_column(self):
        if self.workflow.draft is None:
            return None
        table = self.query_one("#schema", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if row < 0 or row >= len(self.workflow.draft.columns):
            return None
        return self.workflow.draft.columns[row]

    def _summary(self) -> str:
        return (
            "Authoring Workflow\n"
            f"Sample File: {self.workflow.file}\n"
            f"Format: {self.workflow.fmt}\n"
            f"Destination: {self.workflow.dest_table}"
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
