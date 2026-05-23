import json as json_lib
import sys

import click

from etl.compactor import compact as run_compact
from etl.db import Database, SchemaError, create_audit_tables, get_status_summary
from etl.pipeline import run_pipeline


@click.group()
def cli():
    pass


@cli.command()
@click.option("--dir", "watched_dir", required=True, help="Watched directory path")
@click.option("--config", "config_path", required=True, help="Path to pipeline.yaml")
@click.option("--audit-db-url", required=True, envvar="ETL_AUDIT_DB_URL", help="Audit database URL")
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
@click.option("--audit-db-url", required=True, envvar="ETL_AUDIT_DB_URL", help="Audit database URL")
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
