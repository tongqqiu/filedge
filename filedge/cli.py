import json as json_lib
import os
import sys

import click

from filedge.compactor import compact as run_compact
from filedge.connectors import SchemaError
from filedge.audit_records import (
    LineageAmbiguous,
    LineageFound,
    LineageMissing,
    lineage_record,
    status_summary,
)
from filedge.db import (
    Database,
    create_audit_tables,
    find_file_by_hash,
    find_terminal_failed_by_filename,
    list_terminal_failed,
    requeue_all_terminal_failed,
    requeue_by_hash,
)
from filedge.authoring import AuthoringSession
from filedge.config import load_config
from filedge.file_sample import FormatNotDetected, resolve_format
from filedge.pipeline_registry import RegistryError
from filedge.pipeline_resolution import (
    PipelineNotFound,
    ResolvedPipeline,
    resolve_pipeline,
)
from filedge.reference import ReferenceError
from filedge.health import HealthcheckError
from filedge.inspect_formatter import format_summary, format_yaml
from filedge.preview_formatter import format_preview
from filedge.progress import RichPipelineProgress
from filedge.validate_formatter import format_json, format_text
from filedge.pipeline import run_pipeline


_FORMAT_CHOICE = click.Choice(["csv", "ndjson", "parquet", "fixed_width", "excel"])

_FIXED_WIDTH_DOCS = "docs/guides/fixed-width.md"


def _require_format(file: str, fmt: str | None, exit_code: int) -> str:
    """Resolve format or print the standard error and exit with the given code."""
    resolved = resolve_format(file, fmt)
    if isinstance(resolved, FormatNotDetected):
        click.echo(
            f"Error: cannot detect format for {resolved.file!r}. "
            f"Use --format csv or --format ndjson.",
            err=True,
        )
        sys.exit(exit_code)
    return resolved


def _explicit(ctx, name) -> bool:
    """True when an option's value came from the command line, not env/default.

    Lets `--pipeline` coexist with a `FILEDGE_AUDIT_DB_URL` in the environment:
    only an *explicitly typed* `--audit-db-url` conflicts with `--pipeline`.
    """
    return ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE


def _resolve_pipeline_or_exit(workspace, pipeline_id) -> ResolvedPipeline:
    """Resolve a Pipeline id against the Registry, or print the error and exit."""
    try:
        return resolve_pipeline(workspace, pipeline_id)
    except (PipelineNotFound, RegistryError, ReferenceError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _operator_audit_db_url(ctx, pipeline_id, workspace, audit_db_url) -> str:
    """Pick the Audit DB URL from an explicit flag or a `--pipeline` id.

    `--pipeline` and an explicitly passed `--audit-db-url` are mutually
    exclusive; exactly one source must supply the Audit DB.
    """
    if pipeline_id and _explicit(ctx, "audit_db_url"):
        click.echo("Error: pass either --pipeline or --audit-db-url, not both.", err=True)
        sys.exit(1)
    if pipeline_id:
        return _resolve_pipeline_or_exit(workspace, pipeline_id).audit_db_url
    if not audit_db_url:
        click.echo("Error: provide --audit-db-url or --pipeline.", err=True)
        sys.exit(1)
    return audit_db_url


def _operator_run_context(ctx, pipeline_id, workspace, watched_dir, config_path, audit_db_url):
    """Pick (watched_dir, config_path, audit_db_url) from explicit flags or `--pipeline`.

    `--pipeline` is mutually exclusive with any explicitly typed
    `--dir`/`--config`/`--audit-db-url`; exactly one source must supply all three.
    Uses `_explicit` so a `FILEDGE_AUDIT_DB_URL` env var does not falsely conflict.
    """
    if pipeline_id and (
        _explicit(ctx, "watched_dir")
        or _explicit(ctx, "config_path")
        or _explicit(ctx, "audit_db_url")
    ):
        click.echo(
            "Error: pass either --pipeline or --dir/--config/--audit-db-url, not both.",
            err=True,
        )
        sys.exit(1)
    if pipeline_id:
        resolved = _resolve_pipeline_or_exit(workspace, pipeline_id)
        return resolved.watched_directory, resolved.config_path, resolved.audit_db_url
    if not (watched_dir and config_path and audit_db_url):
        click.echo("Error: provide --dir/--config/--audit-db-url or --pipeline.", err=True)
        sys.exit(1)
    return watched_dir, config_path, audit_db_url


def _parse_sheet_selector(value):
    """`--sheet` accepts either an integer (0-based index) or a sheet name."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


@click.group()
def cli():
    pass


@cli.command()
@click.option("--pipeline", "pipeline_id", default=None,
              help="Resolve --dir/--config/--audit-db-url from this Pipeline Registry id.")
@click.option("--workspace", default=".", show_default=True,
              type=click.Path(file_okay=False),
              help="Workspace root holding pipeline-registry.yaml (used with --pipeline).")
@click.option("--dir", "watched_dir", required=False, default=None,
              type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help="Watched directory path")
@click.option("--config", "config_path", required=False, default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to pipeline.yaml")
@click.option("--audit-db-url", required=False, default=None, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
@click.option(
    "--progress/--no-progress",
    "show_progress",
    default=None,
    help="Show live progress bars. Defaults to on for interactive terminals.",
)
@click.option("--json", "output_json", is_flag=True,
              help="Write the Run summary as a single JSON line to stdout. Exit non-zero if any file failed.")
@click.option("--log-format", "log_format", type=click.Choice(["json", "text"]), default=None,
              help="Log output format. Defaults to text on a TTY, json otherwise.")
@click.option("--log-level", "log_level", default="INFO", show_default=True,
              help="Log level (DEBUG, INFO, WARNING, ERROR).")
@click.option("--otel-traces/--no-otel-traces", "otel_traces", default=None,
              help="Enable OpenTelemetry tracing. Off by default. Also enabled by FILEDGE_OTEL_TRACES=true.")
@click.option("--otel-logs/--no-otel-logs", "otel_logs", default=None,
              help="Export filedge logs through OpenTelemetry. Off by default. Also enabled by FILEDGE_OTEL_LOGS=true.")
@click.pass_context
def run(
    ctx,
    pipeline_id,
    workspace,
    watched_dir,
    config_path,
    audit_db_url,
    show_progress,
    output_json,
    log_format,
    log_level,
    otel_traces,
    otel_logs,
):
    """Run the ETL pipeline for a Watched Directory."""
    watched_dir, config_path, audit_db_url = _operator_run_context(
        ctx, pipeline_id, workspace, watched_dir, config_path, audit_db_url
    )

    from filedge.log import configure_logging, get_logger
    from filedge.progress import LoggingProgressReporter
    from filedge.tracing import (
        configure_otel_logs,
        configure_tracing,
        should_enable_logs,
        should_enable_tracing,
    )

    try:
        is_tty = sys.stderr.isatty()
        if show_progress is None:
            show_progress = is_tty
        if log_format is None:
            log_format = "text" if is_tty else "json"

        configure_logging(level=log_level, fmt=log_format)

        tracing_on = should_enable_tracing(
            cli_flag=otel_traces,
            env_value=os.environ.get("FILEDGE_OTEL_TRACES"),
        )
        configure_tracing(enabled=tracing_on)
        logs_on = should_enable_logs(
            cli_flag=otel_logs,
            env_value=os.environ.get("FILEDGE_OTEL_LOGS"),
        )
        configure_otel_logs(enabled=logs_on)

        run_id = _new_run_id()
        log_reporter = LoggingProgressReporter(get_logger("filedge.pipeline"), run_id=run_id)

        from contextlib import ExitStack
        with ExitStack() as stack:
            handlers = [log_reporter.handle]

            tracing_reporter = None
            if tracing_on:
                from filedge.progress import TracingProgressReporter
                tracing_reporter = stack.enter_context(TracingProgressReporter(run_id=run_id))
                handlers.append(tracing_reporter.handle)

            if show_progress:
                from rich.console import Console
                rich_progress = stack.enter_context(RichPipelineProgress(Console(stderr=True)))
                handlers.insert(0, rich_progress.handle)

            result = run_pipeline(
                watched_dir, config_path, audit_db_url,
                progress=_tee(*handlers), run_id=run_id,
            )
            if tracing_reporter is not None:
                tracing_reporter.set_run_attributes(result)

        if output_json:
            click.echo(json_lib.dumps(result))
        else:
            click.echo(
                f"Committed: {result['committed']}  "
                f"Failed: {result['failed']}  "
                f"Skipped: {result['skipped']}  "
                f"New: {result['new_files']}  "
                f"Reclaimed: {result['reclaimed']}  "
                f"Retried: {result['retried']}"
            )

        if result["failed"] > 0:
            sys.exit(1)
    except SchemaError as e:
        click.echo(f"Schema error: {e}", err=True)
        sys.exit(1)
    except HealthcheckError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

def _new_run_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _tee(*handlers):
    def fanout(event):
        for h in handlers:
            h(event)
    return fanout


@cli.command()
@click.option("--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to pipeline.yaml")
@click.option("--audit-db-url", required=True, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
@click.option("--json", "output_json", is_flag=True,
              help="Write health status as a JSON object to stdout.")
def healthcheck(config_path, audit_db_url, output_json):
    """Probe the Audit DB and destination connector without writing data."""
    from filedge.health import run_healthchecks

    try:
        report = run_healthchecks(load_config(config_path), audit_db_url)
    except Exception as e:
        click.echo(f"Healthcheck failed: configuration unreachable: {e}", err=True)
        sys.exit(1)

    if output_json:
        click.echo(json_lib.dumps(report))
    else:
        for check in report["checks"]:
            if check["ok"]:
                click.echo(f"{check['name']}: ok ({check['latency_ms']} ms)")
            else:
                click.echo(
                    f"{check['name']}: unreachable: {check['error']}",
                    err=True,
                )

    if not report["healthy"]:
        sys.exit(1)


@cli.command()
@click.option("--watched-dir", required=True,
              type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help="Source prefix containing small files")
@click.option("--output", required=True, help="Output prefix for compacted files")
@click.option("--max-files", default=1000, show_default=True, help="Max input files per output file")
@click.option("--compress", is_flag=True, help="Gzip-compress output (.ndjson.gz)")
@click.option("--delete-source", is_flag=True,
              help="Delete source files after each batch commits (requires delete permission).")
def compact(watched_dir, output, max_files, compress, delete_source):
    """Merge small NDJSON files into fewer larger files before ingestion."""
    try:
        result = run_compact(watched_dir, output, max_files=max_files, compress=compress,
                             delete_source=delete_source)
        click.echo(
            f"Batches written: {result['batches']}  "
            f"Files compacted: {result['files_compacted']}"
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--pipeline", "pipeline_id", default=None,
              help="Resolve the Audit DB from this Pipeline Registry id instead of --audit-db-url.")
@click.option("--workspace", default=".", show_default=True,
              type=click.Path(file_okay=False),
              help="Workspace root holding pipeline-registry.yaml (used with --pipeline).")
@click.option("--audit-db-url", default=None, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
@click.option("--all", "all_pipelines", is_flag=True,
              help="Fan out across every Pipeline in the Registry. Mutually exclusive "
                   "with --pipeline and --audit-db-url.")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def status(ctx, pipeline_id, workspace, audit_db_url, all_pipelines, output_json):
    """Show pipeline status summary."""
    if all_pipelines:
        _status_all(workspace, output_json, ctx, pipeline_id)
        return

    audit_db_url = _operator_audit_db_url(ctx, pipeline_id, workspace, audit_db_url)
    db = Database(audit_db_url)
    create_audit_tables(db)
    summary = status_summary(db)
    db.close()

    if output_json:
        click.echo(json_lib.dumps(summary, indent=2))
    else:
        _print_status_summary(summary)


def _print_status_summary(summary, prefix=""):
    """Render one Pipeline's state counts and recent failures (human format)."""
    click.echo(f"{prefix}PENDING:    {summary['PENDING']}")
    click.echo(f"{prefix}PROCESSING: {summary['PROCESSING']}")
    click.echo(f"{prefix}COMMITTED:  {summary['COMMITTED']}")
    click.echo(f"{prefix}FAILED:     {summary['FAILED']}")
    if summary["recent_failures"]:
        if not prefix:
            click.echo("")
        click.echo(f"{prefix}Recent failures:")
        for f in summary["recent_failures"]:
            click.echo(f"{prefix}  {f['filename']}: {f['error_message']}")


def _status_all(workspace, output_json, ctx, pipeline_id):
    """Handle `status --all`: validate exclusivity, fan out, and print per Pipeline."""
    if pipeline_id or _explicit(ctx, "audit_db_url"):
        click.echo(
            "Error: --all is mutually exclusive with --pipeline and --audit-db-url.",
            err=True,
        )
        sys.exit(1)

    from filedge.status_all import status_all

    try:
        results = status_all(workspace)
    except (RegistryError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if output_json:
        payload = []
        for r in results:
            if r.error is not None:
                payload.append({"pipeline": r.id, "error": r.error})
            else:
                payload.append({"pipeline": r.id, **r.summary})
        click.echo(json_lib.dumps(payload, indent=2))
        return

    for i, r in enumerate(results):
        if i > 0:
            click.echo("")
        if r.error is not None:
            click.echo(f"{r.id}: ERROR: {r.error}")
        else:
            click.echo(f"{r.id}:")
            _print_status_summary(r.summary, prefix="  ")


@cli.command()
@click.argument("file")
@click.option("--format", "fmt", default=None, type=_FORMAT_CHOICE,
              help="File format (auto-detected from extension)")
@click.option("--sample-rows", default=1000, show_default=True, help="Number of rows to sample")
@click.option("--output", "output_path", default=None,
              type=click.Path(dir_okay=False),
              help="Write YAML block to this file instead of stdout")
@click.option("--encoding", default="utf-8", show_default=True, help="File encoding (e.g. utf-8, cp500, latin-1)")
@click.option("--sheet", default=None,
              help="Excel sheet name or 0-based index (excel format only). Default: first sheet.")
def inspect(file, fmt, sample_rows, output_path, encoding, sheet):
    """Infer schema from a file and output a columns: block for pipeline.yaml."""
    fmt = _require_format(file, fmt, exit_code=1)

    if fmt == "fixed_width":
        click.echo(
            "Error: filedge inspect does not support fixed_width — the layout is not "
            "discoverable from the file. Declare it from your partner record-layout spec "
            f"following {_FIXED_WIDTH_DOCS}.",
            err=True,
        )
        sys.exit(1)

    if sheet is not None and fmt != "excel":
        click.echo("Error: --sheet is only valid with --format excel.", err=True)
        sys.exit(1)

    session = AuthoringSession(
        file, fmt, encoding=encoding, sheet=_parse_sheet_selector(sheet)
    )
    try:
        sheet_for_header = session.sheet_name
        columns = session.infer_schema(sample_rows=sample_rows)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    yaml_block = format_yaml(
        columns,
        source_path=file,
        sample_rows=sample_rows,
        sheet=sheet_for_header,
    )
    summary = format_summary(columns)

    click.echo(summary, err=True)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_block)
    else:
        click.echo(yaml_block, nl=False)


@cli.command()
@click.argument("file")
@click.option("--format", "fmt", default=None, type=_FORMAT_CHOICE,
              help="File format (auto-detected from extension)")
@click.option("--config", "config_path", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to pipeline.yaml — required for --format fixed_width")
@click.option("--rows", "num_rows", default=10, show_default=True, help="Number of rows to display")
@click.option("--start-row", "start_row", default=1, show_default=True, help="First row to display (1-indexed)")
@click.option("--encoding", default="utf-8", show_default=True, help="File encoding (e.g. utf-8, cp500, latin-1)")
@click.option("--sheet", default=None,
              help="Excel sheet name or 0-based index (excel format only). Default: first sheet.")
def preview(file, fmt, config_path, num_rows, start_row, encoding, sheet):
    """Show N rows of a file as a formatted table, optionally starting at a given row."""
    fmt = _require_format(file, fmt, exit_code=2)

    if sheet is not None and fmt != "excel":
        click.echo("Error: --sheet is only valid with --format excel.", err=True)
        sys.exit(2)

    config = None
    if fmt == "fixed_width":
        if not config_path:
            click.echo(
                "Error: --config <pipeline.yaml> is required for --format fixed_width. "
                f"See {_FIXED_WIDTH_DOCS}.",
                err=True,
            )
            sys.exit(2)
        try:
            config = load_config(config_path)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(2)

    session = AuthoringSession(
        file, fmt, config=config, encoding=encoding, sheet=_parse_sheet_selector(sheet)
    )
    try:
        materialized = session.preview(start_row=start_row, num_rows=num_rows)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    click.echo(format_preview(materialized, start_row=start_row))


@cli.command()
@click.argument("file")
@click.option("--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to pipeline.yaml")
@click.option("--format", "fmt", default=None, type=_FORMAT_CHOICE,
              help="File format (auto-detected from extension)")
@click.option("--sample-rows", default=None, type=int, help="Validate only the first N rows")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON to stdout")
@click.option("--encoding", default=None, help="Override file encoding from pipeline.yaml (e.g. cp500)")
@click.option("--sheet", default=None,
              help="Override the excel: sheet from pipeline.yaml (excel format only).")
def validate(file, config_path, fmt, sample_rows, output_json, encoding, sheet):
    """Validate a file against a pipeline.yaml schema without loading it."""
    fmt = _require_format(file, fmt, exit_code=2)

    if sheet is not None and fmt != "excel":
        click.echo("Error: --sheet is only valid with --format excel.", err=True)
        sys.exit(2)

    try:
        config = load_config(config_path)
        session = AuthoringSession(
            file,
            fmt,
            config=config,
            encoding=encoding,
            sheet=_parse_sheet_selector(sheet),
        )
        result = session.validate(sample_rows=sample_rows)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    click.echo(format_text(result), err=True)
    if output_json:
        click.echo(json_lib.dumps(format_json(result)))

    if result.failures:
        sys.exit(1)


@cli.command()
@click.argument("sample_file", required=False)
@click.option("--pipeline", "pipeline", default=None,
              help="Re-author an existing Pipeline Folder (workspace-relative path).")
@click.option("--format", "fmt", default=None, type=_FORMAT_CHOICE,
              help="File format (auto-detected from extension)")
@click.option("--sample-rows", default=1000, show_default=True, help="Number of rows to sample")
@click.option("--dest-table", default=None,
              help="Destination table name. Defaults to the sample File stem.")
@click.option("--out", default=None,
              help="Pipeline Folder id/name override")
@click.option("--workspace", default=".",
              type=click.Path(file_okay=False, dir_okay=True),
              help="Workspace root for Pipeline Folder and Pipeline Registry")
@click.option("--encoding", default=None, help="File encoding override")
@click.option("--sheet", default=None,
              help="Excel sheet name or 0-based index. Default: first sheet.")
def author(sample_file, pipeline, fmt, sample_rows, dest_table, out, workspace, encoding, sheet):
    """Launch the local Authoring UI.

    Pass a SAMPLE_FILE to author a new Pipeline from scratch, or `--pipeline
    <folder>` to re-open an existing Pipeline Folder and revise its config.
    """
    try:
        from filedge.authoring_ui import AuthoringApp
        from filedge.authoring_workflow import AuthoringWorkflow
    except ImportError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if pipeline is not None and sample_file is not None:
        click.echo(
            "Error: pass either a SAMPLE_FILE or --pipeline, not both.", err=True
        )
        sys.exit(2)
    if pipeline is None and sample_file is None:
        from filedge.pipeline_registry import registry_exists

        if not registry_exists(workspace):
            click.echo(
                "Error: pass a SAMPLE_FILE or --pipeline <folder>.", err=True
            )
            sys.exit(2)
        # Pipeline Registry browse-and-pick (#179) — the only place the CLI
        # routes the User into the browse screen.
        from filedge.authoring_browse import (
            NEW_PIPELINE_SENTINEL,
            PipelineBrowseApp,
            list_browse_entries,
        )

        try:
            entries = list_browse_entries(workspace)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(2)
        browse = PipelineBrowseApp(entries)
        browse.run()
        choice = browse.selected_folder
        if choice is None:
            return
        if choice == NEW_PIPELINE_SENTINEL:
            click.echo(
                "Error: from-scratch authoring needs a SAMPLE_FILE. "
                "Re-run: filedge author <SAMPLE_FILE>.",
                err=True,
            )
            sys.exit(2)
        pipeline = choice

    try:
        if pipeline is not None:
            workflow = AuthoringWorkflow.open_folder(
                folder=pipeline,
                workspace=workspace,
                sample_rows=sample_rows,
            )
        else:
            if sheet is not None and fmt is not None and fmt != "excel":
                click.echo(
                    "Error: --sheet is only valid with --format excel.", err=True
                )
                sys.exit(2)
            if dest_table is None:
                dest_table = os.path.splitext(os.path.basename(sample_file))[0]
            workflow = AuthoringWorkflow.start(
                file=sample_file,
                workspace=workspace,
                dest_table=dest_table,
                fmt=fmt,
                sample_rows=sample_rows,
                encoding=encoding,
                sheet=_parse_sheet_selector(sheet),
                out=out,
            )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    AuthoringApp(workflow).run()


@cli.command()
@click.option(
    "--shell",
    type=click.Choice(["zsh", "bash"]),
    default=None,
    help="Shell type (auto-detected from $SHELL if omitted)",
)
def completion(shell):
    """Print shell completion script.

    \b
    Zsh:  filedge completion >> ~/.zshrc && source ~/.zshrc
    Bash: filedge completion --shell bash >> ~/.bashrc && source ~/.bashrc
    """
    if shell is None:
        detected = os.environ.get("SHELL", "")
        if "zsh" in detected:
            shell = "zsh"
        elif "bash" in detected:
            shell = "bash"
        else:
            raise click.UsageError(
                "Cannot detect shell from $SHELL. Use --shell zsh or --shell bash."
            )

    from click.shell_completion import BashComplete, ZshComplete

    cls = ZshComplete if shell == "zsh" else BashComplete
    click.echo(cls(cli, {}, "filedge", "_FILEDGE_COMPLETE").source(), nl=False)


@cli.command()
@click.argument("filename", required=False)
@click.option("--hash", "content_hash", default=None,
              help="Content hash to disambiguate when multiple records share the same filename")
@click.option("--all-terminal-failed", "all_terminal_failed", is_flag=True,
              help="Requeue all terminal-FAILED files")
@click.option("--dry-run", is_flag=True,
              help="List files that would be requeued without making changes (requires --all-terminal-failed)")
@click.option("--yes", is_flag=True,
              help="Confirm bulk requeue (required with --all-terminal-failed)")
@click.option("--retry-cap", default=3, show_default=True,
              help="Retry cap used to identify terminal-FAILED files; must match pipeline.yaml")
@click.option("--pipeline", "pipeline_id", default=None,
              help="Resolve the Audit DB from this Pipeline Registry id instead of --audit-db-url.")
@click.option("--workspace", default=".", show_default=True,
              type=click.Path(file_okay=False),
              help="Workspace root holding pipeline-registry.yaml (used with --pipeline).")
@click.option("--audit-db-url", default=None, envvar="FILEDGE_AUDIT_DB_URL",
              help="Audit database URL")
@click.pass_context
def requeue(ctx, filename, content_hash, all_terminal_failed, dry_run, yes, retry_cap, pipeline_id, workspace, audit_db_url):
    """Requeue terminal-FAILED files so they are retried on the next run.

    \b
    Single file:
      filedge requeue orders.csv
      filedge requeue orders.csv --hash a1b2c3...  # disambiguate duplicate filenames

    \b
    Bulk:
      filedge requeue --all-terminal-failed           # preview count
      filedge requeue --all-terminal-failed --dry-run # list affected files
      filedge requeue --all-terminal-failed --yes     # execute
    """
    if filename and all_terminal_failed:
        click.echo("Error: provide either a filename or --all-terminal-failed, not both.", err=True)
        sys.exit(1)
    if not filename and not all_terminal_failed:
        click.echo("Error: provide a filename or --all-terminal-failed.", err=True)
        sys.exit(1)
    if filename and dry_run:
        click.echo("Error: --dry-run is only valid with --all-terminal-failed.", err=True)
        sys.exit(1)
    if filename and yes:
        click.echo("Error: --yes is only valid with --all-terminal-failed.", err=True)
        sys.exit(1)
    if dry_run and yes:
        click.echo("Error: --dry-run and --yes are mutually exclusive.", err=True)
        sys.exit(1)

    audit_db_url = _operator_audit_db_url(ctx, pipeline_id, workspace, audit_db_url)

    db = Database(audit_db_url)
    create_audit_tables(db)

    try:
        if all_terminal_failed:
            records = list_terminal_failed(db, retry_cap)

            if dry_run:
                if not records:
                    click.echo("No terminal-FAILED files found.")
                    return
                for r in records:
                    click.echo(f"  {r.filename}  {r.content_hash}  {r.error_message or ''}")
                click.echo(
                    f"\nWould requeue {len(records)} file(s). Re-run with --yes to proceed."
                )
                return

            if not yes:
                count = len(records)
                if count == 0:
                    click.echo("No terminal-FAILED files found.")
                    return
                click.echo(
                    f"Found {count} terminal-FAILED file(s). Re-run with --yes to requeue."
                )
                sys.exit(1)

            n = requeue_all_terminal_failed(db, retry_cap)
            db.commit()
            click.echo(f"Requeued: {n}")

        else:
            if content_hash:
                record = find_file_by_hash(db, content_hash)
                if record is None:
                    click.echo(f"Error: no record found for hash {content_hash!r}.", err=True)
                    sys.exit(1)
                if record.state != "FAILED" or record.attempt_count < retry_cap:
                    click.echo(
                        f"Error: {record.filename!r} is in state {record.state!r} with"
                        f" attempt_count={record.attempt_count} — not eligible for requeue"
                        f" (retry_cap={retry_cap}).",
                        err=True,
                    )
                    sys.exit(1)
                requeue_by_hash(db, content_hash)
                db.commit()
                click.echo(f"Requeued: {record.filename} ({content_hash[:12]}…)")
            else:
                records = find_terminal_failed_by_filename(db, filename, retry_cap)
                if not records:
                    click.echo(
                        f"Error: no terminal-FAILED record found for {filename!r}.", err=True
                    )
                    sys.exit(1)
                if len(records) > 1:
                    click.echo(
                        f"Error: {len(records)} terminal-FAILED records found for {filename!r}."
                        f" Use --hash to disambiguate:",
                        err=True,
                    )
                    for r in records:
                        click.echo(
                            f"  --hash {r.content_hash}  (error: {r.error_message or 'unknown'})",
                            err=True,
                        )
                    sys.exit(1)
                record = records[0]
                requeue_by_hash(db, record.content_hash)
                db.commit()
                click.echo(f"Requeued: {record.filename} ({record.content_hash[:12]}…)")
    finally:
        db.close()


@cli.command()
@click.argument("identifier")
@click.option("--pipeline", "pipeline_id", default=None,
              help="Resolve the Audit DB from this Pipeline Registry id instead of --audit-db-url.")
@click.option("--workspace", default=".", show_default=True,
              type=click.Path(file_okay=False),
              help="Workspace root holding pipeline-registry.yaml (used with --pipeline).")
@click.option("--audit-db-url", default=None, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON")
@click.option("--dest-table", default=None, help="Destination table name to include in lineage output")
@click.pass_context
def lineage(ctx, identifier, pipeline_id, workspace, audit_db_url, output_json, dest_table):
    """Show the full audit + source-manifest lineage for one File.

    IDENTIFIER may be a Content Hash or a filename. When a filename matches
    multiple Content Hashes, the command prints a disambiguation list and
    exits non-zero — re-run with the specific Content Hash to drill in.
    """
    audit_db_url = _operator_audit_db_url(ctx, pipeline_id, workspace, audit_db_url)
    db = Database(audit_db_url)
    try:
        create_audit_tables(db)
        result = lineage_record(db, identifier)
        if isinstance(result, LineageMissing):
            click.echo(f"No File found matching {identifier!r}", err=True)
            sys.exit(1)
        if isinstance(result, LineageAmbiguous):
            click.echo(
                f"Filename {identifier!r} maps to {len(result.matches)} Content Hashes — "
                "re-run with one of these Content Hashes:",
                err=True,
            )
            for match in result.matches:
                click.echo(
                    f"  {match.content_hash}  state={match.state}",
                    err=True,
                )
            sys.exit(2)
        assert isinstance(result, LineageFound)
        if output_json:
            click.echo(json_lib.dumps(
                _lineage_payload(
                    result.record,
                    result.run_id,
                    result.created_at,
                    result.updated_at,
                    dest_table,
                ),
                indent=2,
            ))
        else:
            _print_lineage_human(
                result.record,
                result.run_id,
                result.created_at,
                result.updated_at,
                dest_table,
            )
    finally:
        db.close()


def _print_lineage_human(record, run_id, created_at, updated_at, dest_table):
    click.echo(f"filename:         {record.filename}")
    click.echo(f"content_hash:     {record.content_hash}")
    click.echo(f"state:            {record.state}")
    click.echo(f"attempt_count:    {record.attempt_count}")
    click.echo(f"row_count:        {record.row_count if record.row_count is not None else '-'}")
    click.echo(f"dest_table:       {dest_table or '-'}")
    click.echo(f"error_message:    {record.error_message or '-'}")
    click.echo(f"run_id:           {run_id or '-'}")
    click.echo(f"created_at:       {created_at or '-'}")
    click.echo(f"updated_at:       {updated_at or '-'}")
    click.echo(f"claimed_at:       {record.claimed_at or '-'}")
    click.echo("")
    click.echo("Source manifest:")
    if record.source_type is None and record.source_name is None:
        click.echo("  (no manifest attached)")
        return
    click.echo(f"  manifest_version: {record.manifest_version or '-'}")
    click.echo(f"  source_type:      {record.source_type or '-'}")
    click.echo(f"  source_name:      {record.source_name or '-'}")
    click.echo(f"  producer:         {record.producer or '-'}")
    click.echo(f"  external_run_id:  {record.external_run_id or '-'}")
    click.echo(f"  started_at:       {record.started_at or '-'}")
    click.echo(f"  finished_at:      {record.finished_at or '-'}")
    click.echo(f"  record_count:     {record.record_count if record.record_count is not None else '-'}")
    if record.source_range:
        click.echo("  source_range:")
        for k, v in record.source_range.items():
            click.echo(f"    {k}: {v}")
    else:
        click.echo("  source_range:     -")


def _lineage_payload(record, run_id, created_at, updated_at, dest_table):
    if record.source_type is None and record.source_name is None:
        source_manifest = None
    else:
        source_manifest = {
            "manifest_version": record.manifest_version,
            "source_type": record.source_type,
            "source_name": record.source_name,
            "producer": record.producer,
            "external_run_id": record.external_run_id,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "record_count": record.record_count,
            "source_range": record.source_range,
        }
    return {
        "filename": record.filename,
        "content_hash": record.content_hash,
        "state": record.state,
        "attempt_count": record.attempt_count,
        "row_count": record.row_count,
        "error_message": record.error_message,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "claimed_at": record.claimed_at,
        "dest_table": dest_table,
        "source_manifest": source_manifest,
    }

@cli.command("export-audit")
@click.option("--audit-db-url", required=True, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
@click.option("--output", required=True, help="Output path for index.html")
@click.option("--title", default=None, help="Pipeline label shown in the site header")
@click.option("--dest-table", default=None, help="Destination table name for lineage SQL")
def export_audit_cmd(audit_db_url, output, title, dest_table):
    """Generate a read-only static HTML Audit Export from the Audit DB."""
    from filedge.db import Database, create_audit_tables
    from filedge.exporter import export_audit

    db = Database(audit_db_url)
    try:
        create_audit_tables(db)
        count = export_audit(db, output, title=title, dest_table=dest_table)
        click.echo(f"Exported {count} file records to {output}")
    finally:
        db.close()
