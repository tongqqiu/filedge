import json as json_lib
import os
import sys

import click

from filedge.compactor import compact as run_compact
from filedge.connectors import SchemaError
from filedge.db import Database, create_audit_tables, get_status_summary
from filedge.filesystem import get_filesystem, open_file
from filedge.config import load_config
from filedge.inferrer import infer_schema
from filedge.inspect_formatter import format_summary, format_yaml
from filedge.parser import get_parser
from filedge.preview_formatter import format_preview
from filedge.validate_formatter import format_json, format_text
from filedge.validator import validate_file
from filedge.pipeline import run_pipeline

_EXT_TO_FORMAT = {
    ".csv": "csv",
    ".ndjson": "ndjson",
    ".jsonl": "ndjson",
}


@click.group()
def cli():
    pass


@cli.command()
@click.option("--dir", "watched_dir", required=True, help="Watched directory path")
@click.option("--config", "config_path", required=True, help="Path to pipeline.yaml")
@click.option("--audit-db-url", required=True, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
def run(watched_dir, config_path, audit_db_url):
    """Run the ETL pipeline for a Watched Directory."""
    try:
        result = run_pipeline(watched_dir, config_path, audit_db_url)
        click.echo(
            f"Committed: {result['committed']}  "
            f"Failed: {result['failed']}  "
            f"Skipped: {result['skipped']}  "
            f"New: {result['new_files']}  "
            f"Reclaimed: {result['reclaimed']}  "
            f"Retried: {result['retried']}"
        )
    except SchemaError as e:
        click.echo(f"Schema error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--watched-dir", required=True, help="Source prefix containing small files")
@click.option("--output", required=True, help="Output prefix for compacted files")
@click.option("--max-files", default=1000, show_default=True, help="Max input files per output file")
@click.option("--compress", is_flag=True, help="Gzip-compress output (.ndjson.gz)")
def compact(watched_dir, output, max_files, compress):
    """Merge small NDJSON files into fewer larger files before ingestion."""
    try:
        result = run_compact(watched_dir, output, max_files=max_files, compress=compress)
        click.echo(
            f"Batches written: {result['batches']}  "
            f"Files compacted: {result['files_compacted']}"
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--audit-db-url", required=True, envvar="FILEDGE_AUDIT_DB_URL", help="Audit database URL")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def status(audit_db_url, output_json):
    """Show pipeline status summary."""
    db = Database(audit_db_url)
    create_audit_tables(db)
    summary = get_status_summary(db)
    db.close()

    if output_json:
        click.echo(json_lib.dumps(summary, indent=2))
    else:
        click.echo(f"PENDING:    {summary['PENDING']}")
        click.echo(f"PROCESSING: {summary['PROCESSING']}")
        click.echo(f"COMMITTED:  {summary['COMMITTED']}")
        click.echo(f"FAILED:     {summary['FAILED']}")
        if summary["recent_failures"]:
            click.echo("\nRecent failures:")
            for f in summary["recent_failures"]:
                click.echo(f"  {f['filename']}: {f['error_message']}")


@cli.command()
@click.argument("file")
@click.option("--format", "fmt", default=None, help="File format: csv or ndjson (auto-detected from extension)")
@click.option("--sample-rows", default=1000, show_default=True, help="Number of rows to sample")
@click.option("--output", "output_path", default=None, help="Write YAML block to this file instead of stdout")
def inspect(file, fmt, sample_rows, output_path):
    """Infer schema from a file and output a columns: block for pipeline.yaml."""
    if fmt is None:
        _, ext = os.path.splitext(file)
        fmt = _EXT_TO_FORMAT.get(ext.lower())
        if fmt is None:
            click.echo(
                f"Error: cannot detect format for {file!r}. "
                f"Use --format csv or --format ndjson.",
                err=True,
            )
            sys.exit(1)

    try:
        fs, path = get_filesystem(file)
        parser = get_parser(fmt)
        with open_file(path, fs=fs) as f:
            columns = infer_schema(parser.parse(f), sample_rows=sample_rows)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    yaml_block = format_yaml(columns, source_path=file, sample_rows=sample_rows)
    summary = format_summary(columns)

    click.echo(summary, err=True)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_block)
    else:
        click.echo(yaml_block, nl=False)


@cli.command()
@click.argument("file")
@click.option("--format", "fmt", default=None, help="File format: csv or ndjson (auto-detected from extension)")
@click.option("--rows", "num_rows", default=10, show_default=True, help="Number of rows to display")
def preview(file, fmt, num_rows):
    """Show the first N rows of a file as a formatted table."""
    if fmt is None:
        _, ext = os.path.splitext(file)
        fmt = _EXT_TO_FORMAT.get(ext.lower())
        if fmt is None:
            click.echo(
                f"Error: cannot detect format for {file!r}. "
                f"Use --format csv or --format ndjson.",
                err=True,
            )
            sys.exit(2)

    try:
        from itertools import islice
        fs, path = get_filesystem(file)
        parser = get_parser(fmt)
        with open_file(path, fs=fs) as f:
            rows = list(islice(parser.parse(f), num_rows))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    click.echo(format_preview(rows))


@cli.command()
@click.argument("file")
@click.option("--config", "config_path", required=True, help="Path to pipeline.yaml")
@click.option("--format", "fmt", default=None, help="File format: csv or ndjson (auto-detected from extension)")
@click.option("--sample-rows", default=None, type=int, help="Validate only the first N rows")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON to stdout")
def validate(file, config_path, fmt, sample_rows, output_json):
    """Validate a file against a pipeline.yaml schema without loading it."""
    if fmt is None:
        _, ext = os.path.splitext(file)
        fmt = _EXT_TO_FORMAT.get(ext.lower())
        if fmt is None:
            click.echo(
                f"Error: cannot detect format for {file!r}. "
                f"Use --format csv or --format ndjson.",
                err=True,
            )
            sys.exit(2)

    try:
        config = load_config(config_path)
        fs, path = get_filesystem(file)
        parser = get_parser(fmt)
        with open_file(path, fs=fs) as f:
            rows = parser.parse(f)
            if sample_rows is not None:
                from itertools import islice
                rows = islice(rows, sample_rows)
            result = validate_file(rows, config.columns)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    click.echo(format_text(result), err=True)
    if output_json:
        click.echo(json_lib.dumps(format_json(result)))

    if result.failures:
        sys.exit(1)
