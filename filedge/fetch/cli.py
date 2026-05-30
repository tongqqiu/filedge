"""The `filedge-fetch` command — the Reference Fetcher's own entry point.

A separate console script, not a `filedge` subcommand: the Fetcher is an
external companion to the ingestion path, never a loader of record (ADR-0006,
ADR-0018). It pulls one API Source from a Sources Config and lands a complete
File (plus its Source Manifest) in the Watched Directory; `filedge run` ingests
it from there exactly like a file drop.
"""

import sys

import click

from filedge.fetch.errors import FetchError
from filedge.fetch.orchestrator import run_fetch


@click.command()
@click.option("--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to sources.yaml (the Sources Config).")
@click.option("--source", "source_name", required=True,
              help="Name of the API Source in the Sources Config to fetch.")
@click.option("--dry-run", is_flag=True,
              help="Report the window and target File without fetching.")
def fetch(config_path, source_name, dry_run):
    """Fetch one API Source into complete NDJSON Files in its Watched Directory."""
    try:
        outcome = run_fetch(config_path, source_name, dry_run=dry_run)
    except FetchError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if outcome.dry_run:
        click.echo(
            f"[dry-run] {outcome.source_name}: would fetch from cursor "
            f"{outcome.from_cursor!r}; target file ~ {outcome.target_filename}"
        )
        return
    if outcome.skipped:
        click.echo(
            f"{outcome.source_name}: no new records since cursor "
            f"{outcome.from_cursor!r}; nothing promoted."
        )
        return
    click.echo(
        f"{outcome.source_name}: fetched {outcome.record_count} records "
        f"({outcome.from_cursor!r} -> {outcome.to_cursor!r}); promoted "
        f"{outcome.data_path}"
    )


if __name__ == "__main__":
    fetch()
