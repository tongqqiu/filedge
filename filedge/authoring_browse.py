"""Pipeline Registry browse-and-pick screen for the Authoring UI (#179).

Renders each registered Pipeline with the four columns the Authoring User picks
between by — folder name, last-author timestamp, format, connector type — and a
"New Pipeline" entry. The browse screen is a pure navigation slice: it never
opens an Audit DB, contacts a Destination, or rewrites authored artifacts.

Pipelines whose Folder is missing on disk are kept in the list and marked
unopenable rather than hidden — a deleted Folder is information the User must
see to act on, not a row to suppress.
"""

import os
from dataclasses import dataclass
from typing import Optional

import yaml

from filedge.pipeline_folder import (
    CONFIG_FILENAME,
    RUNBOOK_FILENAME,
    read_runbook_authored_at,
)
from filedge.pipeline_registry import (
    PipelineRegistry,
    RegistryEntry,
    load_registry,
    parse_registry,
    registry_exists,
    registry_path,
)


NEW_PIPELINE_SENTINEL = "__new__"


@dataclass(frozen=True)
class PipelineBrowseEntry:
    """One row on the Pipeline Registry browse screen.

    ``openable`` is False when the Folder is missing or its ``pipeline.yaml`` is
    unreadable; ``message`` carries the user-facing reason the row is greyed.
    """

    id: str
    folder: str
    last_authored: Optional[str]
    format: Optional[str]
    connector_type: Optional[str]
    openable: bool
    message: str = ""


def list_browse_entries(workspace: str) -> list[PipelineBrowseEntry]:
    """Summarise every Registry entry for the browse screen.

    Loads the Registry *without* the strict folder-exists check so a deleted
    Folder surfaces as an unopenable row instead of failing the whole listing.
    Per-entry IO (reading ``pipeline.yaml`` and ``RUNBOOK.md``) is tolerated: an
    unreadable file becomes a greyed row, never a crash.
    """
    path = registry_path(workspace)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No Pipeline Registry at {path!r}.")
    with open(path) as f:
        data = yaml.safe_load(f)
    registry: PipelineRegistry = parse_registry(data, workspace=None)
    return [_summarise(workspace, entry) for entry in registry.entries]


def _summarise(workspace: str, entry: RegistryEntry) -> PipelineBrowseEntry:
    folder_abs = os.path.join(workspace, entry.folder)
    config_path = os.path.join(folder_abs, CONFIG_FILENAME)
    runbook_path = os.path.join(folder_abs, RUNBOOK_FILENAME)

    if not os.path.isdir(folder_abs):
        return PipelineBrowseEntry(
            id=entry.id,
            folder=entry.folder,
            last_authored=None,
            format=None,
            connector_type=None,
            openable=False,
            message=f"Folder {entry.folder!r} is missing on disk.",
        )
    if not os.path.isfile(config_path):
        return PipelineBrowseEntry(
            id=entry.id,
            folder=entry.folder,
            last_authored=None,
            format=None,
            connector_type=None,
            openable=False,
            message=f"Folder {entry.folder!r} lacks {CONFIG_FILENAME}.",
        )

    fmt: Optional[str] = None
    connector_type: Optional[str] = None
    try:
        with open(config_path) as f:
            config_data = yaml.safe_load(f) or {}
        fmt = config_data.get("format")
        connector = config_data.get("connector") or {}
        connector_type = connector.get("type") if isinstance(connector, dict) else None
    except (OSError, yaml.YAMLError):
        return PipelineBrowseEntry(
            id=entry.id,
            folder=entry.folder,
            last_authored=None,
            format=None,
            connector_type=None,
            openable=False,
            message=f"{CONFIG_FILENAME} for {entry.folder!r} is unreadable.",
        )

    last_authored: Optional[str] = None
    if os.path.isfile(runbook_path):
        try:
            with open(runbook_path) as f:
                last_authored = read_runbook_authored_at(f.read())
        except OSError:
            last_authored = None

    return PipelineBrowseEntry(
        id=entry.id,
        folder=entry.folder,
        last_authored=last_authored,
        format=fmt,
        connector_type=connector_type,
        openable=True,
    )


try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Footer, Header, Static
except ImportError:  # pragma: no cover - covered through the CLI fallback
    App = None  # type: ignore[assignment]


if App is not None:  # pragma: no branch - guard for the optional extra

    class PipelineBrowseApp(App):
        """Textual screen listing Pipelines so the User picks one or "New".

        Returns the selected Pipeline's workspace-relative folder path via
        ``selected_folder``; the ``NEW_PIPELINE_SENTINEL`` signals the from-scratch
        flow. Unopenable Pipelines are listed but cannot be activated.
        """

        CSS = """
        Screen { layout: vertical; }
        #pipelines { height: 1fr; }
        Static { padding: 0 1; }
        """

        BINDINGS = [
            Binding("enter", "open", "Open", priority=True),
            Binding("n", "new", "New Pipeline", priority=True),
            ("q", "quit", "Quit"),
        ]

        def __init__(self, entries: list[PipelineBrowseEntry]):
            super().__init__()
            self.entries = entries
            self.selected_folder: Optional[str] = None
            self.message: str = ""

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static(
                "Pipeline Registry — select a Pipeline to re-author, or pick "
                "New Pipeline to start from a sample File.",
                id="title",
            )
            yield DataTable(id="pipelines")
            yield Static("", id="status")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#pipelines", DataTable)
            table.add_columns(
                "Pipeline", "Last authored", "Format", "Connector", "Status"
            )
            for entry in self.entries:
                table.add_row(
                    entry.folder,
                    entry.last_authored or "(unknown)",
                    entry.format or "-",
                    entry.connector_type or "-",
                    "ok" if entry.openable else f"unopenable: {entry.message}",
                )
            table.add_row("[New Pipeline]", "", "", "", "press Enter to start fresh")
            table.focus()

        def action_open(self) -> None:
            table = self.query_one("#pipelines", DataTable)
            row = table.cursor_row if table.cursor_row is not None else 0
            if row == len(self.entries):
                self.selected_folder = NEW_PIPELINE_SENTINEL
                self.exit()
                return
            if row < 0 or row > len(self.entries):
                return
            entry = self.entries[row]
            if not entry.openable:
                self.query_one("#status", Static).update(
                    f"Pipeline {entry.folder!r} cannot be opened: {entry.message}"
                )
                return
            self.selected_folder = entry.folder
            self.exit()

        def action_new(self) -> None:
            self.selected_folder = NEW_PIPELINE_SENTINEL
            self.exit()


__all__ = [
    "NEW_PIPELINE_SENTINEL",
    "PipelineBrowseEntry",
    "list_browse_entries",
    "registry_exists",
    "load_registry",
]
if App is not None:  # pragma: no branch
    __all__.append("PipelineBrowseApp")
