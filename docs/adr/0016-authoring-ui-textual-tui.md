# ADR-0016: Authoring UI Is a Textual TUI Launched from the Operator CLI

**Status:** Accepted

## Context

ADR-0015 commits Filedge to a local Pipeline Authoring surface as the first step of the Control and Audit Platform, but it deliberately defers what the Authoring UI is *made of*. Before any Authoring Workflow code lands, four narrower questions must be settled:

1. **UI shell technology.** Terminal-based (Textual), local browser (Flask + HTMX), Streamlit, or sequential Click prompts?
2. **Launch command.** What does the user type, and how does it fit the existing Operator CLI vocabulary?
3. **Initial packaging.** Does the UI shell ship in the base `filedge` install, or behind an optional extra alongside the existing connector extras (`postgres`, `bigquery`, `databricks`, `duckdb`, `excel`, …)?
4. **Test surface.** How do we honor the PRD's requirement that "most behavior is testable without browser automation" while still exercising the UI end-to-end?

The choice has long-lived consequences. The Authoring UI is the visible face of the Control and Audit Platform; whatever ships first shapes what target users expect future UI surfaces to look like and how reviewers read the boundary between "tool that helps you author Pipeline Configs" and "platform that runs your ingestion."

## Decision

The Authoring UI is a **Textual** terminal user interface, launched via `filedge author <sample-file>`, shipped behind an optional `authoring` extra.

### UI shell: Textual

The first Authoring UI is built on the [Textual](https://textual.textualize.io) Python TUI framework. The UI is a terminal app that runs in the same shell the Operator CLI lives in.

Three properties drove this choice:

- **Most CLI-adjacent.** ADR-0015 calls for an interface that is "local and CLI-adjacent rather than hosted." A terminal app shares the user's existing shell, working directory, environment variables, and authentication context. It cannot be mistaken for a hosted product.
- **Pure-Python testability.** Textual ships a `Pilot` harness that drives the UI from pytest in-process. The PRD asks for "most behavior testable without browser automation"; Textual delivers that directly. The deep modules from #146, #147, and #148 stay covered by ordinary Python tests; the UI shell adds a small Pilot-driven end-to-end layer for the Authoring Workflow happy path and a few failure surfaces.
- **Lightweight dependency surface.** Textual brings in `rich` (already a Filedge dependency) and a handful of small pure-Python libraries. No browser runtime, no Node, no bundler, no tornado event loop.

The UI shell is *only* the surface that renders panes and routes keystrokes. Schema Inference, Authoring Validation, the Pipeline Config draft model, Pipeline Folder writing, and Pipeline Registry I/O all live in deep modules under `filedge.*` and are reused unchanged from the Operator CLI. The UI does not own any domain rule.

### Launch command: `filedge author <sample-file>`

The Authoring UI is invoked through the existing `filedge` Click entry point as a new subcommand:

```
filedge author <sample-file> [--format csv|ndjson|parquet|excel|fixed_width] [--sample-rows N] [--out <pipeline-folder>]
```

`author` is the verb that matches CONTEXT.md vocabulary (Authoring User, Pipeline Authoring, Authoring Workflow, Authoring Validation, Authoring Runbook). It sits alongside the existing verbs (`inspect`, `preview`, `validate`, `healthcheck`, `run`, `compact`, `requeue`, `status`, `export-audit`) without overlap. The first positional argument is a sample File path, consistent with the PRD principle that the Authoring Workflow starts from a File rather than a blank form.

`filedge author` never executes ingestion, never mutates Audit Records, never reads or writes secret material. Those guarantees are part of the contract of this subcommand and are asserted by regression tests in #149.

### Initial packaging: optional `authoring` extra

The UI shell ships behind an optional extra:

```
pip install filedge[authoring]
```

This matches the established Filedge pattern: every weight-bearing dependency (each Connector backend, Excel, Parquet, cloud filesystems, OpenTelemetry) is opt-in. Pure-CLI users do not carry the Textual dep weight; users who want the Authoring UI ask for it explicitly.

The PRD permits ("may live in the main package initially with optional packaging if UI dependencies require it") rather than requires bundling in the base package. Behavior drift between CLI and UI — the concern that motivated that wording — is prevented architecturally by the rule that the UI shell imports the same `filedge.*` deep modules as the CLI. Only the UI shell itself (Textual screens, widgets, key bindings) sits behind the extra. There is no domain logic in the extra to drift.

When the user runs `filedge author` without the extra installed, the subcommand prints a clear error pointing at `pip install filedge[authoring]`, the same pattern Filedge already uses for missing Connector SDKs.

### Test surface

Three layers of tests, with the first two covering the bulk of behavior:

1. **Deep-module tests in plain pytest.** Authoring session, Pipeline Config draft builder, Authoring Validation service, Pipeline Folder writer, Authoring Runbook renderer, Pipeline Registry I/O are tested directly in Python with no UI in scope. This is where Column Tolerance, Strict Mode coercion, Confidence Tier surfacing rules, CDC required-settings rules, and Field Encryption structural validation are exercised.
2. **UI shell tests via Textual's `Pilot`.** A small end-to-end Pilot test covers the Authoring Workflow happy path (sample CSV → schema review → green Authoring Validation → generated Pipeline Folder and Pipeline Registry entry) and a few failure paths (required-missing-column failure surfaced; unacknowledged low/ambiguous Confidence Tier blocks artifact generation). These tests run in-process without a browser.
3. **Regression invariants.** Tests assert the UI shell never invokes `filedge run`, never opens an Audit DB write connection, and never serializes a secret value into a generated artifact. These guarantees come from ADR-0015 and the PRD and are encoded as tests rather than left to code review.

## Consequences

- **The Authoring UI runs in the terminal.** Users do not get a browser-based form-editing experience for the column review table. Textual's `DataTable` and modal editing primitives are expected to be sufficient for the Authoring Workflow surface; if a future feature requires browser-grade UX (rich diff views, drag-and-drop file pickers, embedded charts), it will require a separate ADR re-opening the UI-shell question.
- **Filedge gains a UI dependency, but only opt-in.** Base `pip install filedge` is unchanged. `pip install filedge[authoring]` is the entry point for the new surface.
- **Textual becomes a supported runtime concern.** Textual upgrades, terminal-compatibility issues (Windows Terminal, iTerm2, generic xterm), and accessibility for terminal users are now in scope for Filedge maintenance.
- **The Operator CLI grows one verb.** `filedge author` is added to the existing verb list. The CLI remains the stable entry point for both ingestion and authoring; there is no second binary, no second installation, no second mental model.
- **No hosted, no browser, no server.** Filedge does not bind to a port, does not open a browser, does not write authentication code, does not write a session layer. The "hosted platform" door stays closed until a future ADR explicitly opens it.
- **Future surfaces can choose differently.** A future read-only Audit Export style surface, a future hosted Control and Audit Platform UI, or a future browser-based authoring view can each pick its own shell. ADR-0016 commits only the first Authoring UI to Textual; it does not commit Filedge to terminal-only forever.

## Alternatives Considered

**Local web with Flask (or FastAPI) + HTMX.** Spin up a localhost HTTP server, open a browser tab for the Authoring Workflow. Richer form UX for editing column tables, easier screenshots for documentation. Rejected as the first surface for three reasons: (a) running a local web server and opening a browser perceptually edges into the "hosted UI" territory ADR-0015 explicitly closes, even though localhost-only is technically not hosted; (b) browser UI invites feature creep toward read-write operations dashboards, which ADR-0015 also rejects; (c) browser-based testing requires Playwright or similar, which conflicts with the PRD's "most behavior testable without browser automation" line. May be revisited if a future surface explicitly needs browser-grade UX.

**Streamlit.** Quick to build, common in data tooling. Rejected because (a) the dep tree is heavy (tornado, altair, watchdog, pyarrow as a transitive in some configs), making the "optional extra" weight much larger than Textual; (b) Streamlit's reactive model encourages mixing UI and logic in a single script, working against the "thin shell over deep modules" rule the PRD makes load-bearing; (c) Streamlit also opens a browser, so it inherits the same hosted-UI perception issue as the Flask option without the corresponding UX benefit.

**Click prompts only.** Use Click's `prompt()` and `confirm()` inline to walk the user through the Authoring Workflow as a sequence of questions. Zero new dependencies. Rejected because authoring is not linear: the user wants to review a table of inferred columns, jump back and forth between columns, see Authoring Validation results alongside the column list, and choose Write Mode and connector settings without restarting the sequence. Sequential prompts model that flow badly. Click prompts may still appear inside the Textual UI for narrow confirmations.

**Rich-only custom REPL.** Use Rich (already a dependency) to render panes and roll a custom REPL loop. Rejected because this reinvents the parts of Textual that already exist — focus management, key binding routing, modal screens, in-process test harness — without the corresponding maintenance benefit. Rich remains the rendering layer underneath Textual; we just do not build a TUI framework on top of it ourselves.
