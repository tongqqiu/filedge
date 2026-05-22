import json as json_lib
import sys

import click

from etl.db import Database, SchemaError, create_audit_tables, get_status_summary
from etl.pipeline import run_pipeline


@click.group()
def cli():
    pass


@cli.command()
@click.option("--dir", "watched_dir", required=True, help="Watched directory path")
@click.option("--config", "config_path", required=True, help="Path to pipeline.yaml")
@click.option("--db-url", required=True, envvar="ETL_DB_URL", help="Database URL")
def run(watched_dir, config_path, db_url):
    """Run the ETL pipeline for a Watched Directory."""
    try:
        result = run_pipeline(watched_dir, config_path, db_url)
        click.echo(
            f"Committed: {result['committed']}  "
            f"Failed: {result['failed']}  "
            f"Skipped: {result['skipped']}  "
            f"New: {result['new_files']}  "
            f"Reclaimed: {result['reclaimed']}"
        )
    except SchemaError as e:
        click.echo(f"Schema error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--db-url", required=True, envvar="ETL_DB_URL", help="Database URL")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def status(db_url, output_json):
    """Show pipeline status summary."""
    db = Database(db_url)
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
