"""The `filedge-materialize` command — the Reference Queue Materializer's entry point.

A separate console script, not a `filedge` subcommand: the Materializer is an
external companion to the ingestion path, never a loader of record (ADR-0007,
ADR-0018). It consumes one Kafka Queue Source from a Sources Config and lands
complete NDJSON Files (each with a Source Manifest) in the Watched Directory;
`filedge run` ingests them from there exactly like a file drop.
"""

import sys

import click

from filedge.companion.errors import CompanionError
from filedge.materialize.errors import MaterializeError
from filedge.materialize.orchestrator import run_materialize


@click.command()
@click.option("--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to sources.yaml (the Sources Config).")
@click.option("--source", "source_name", required=True,
              help="Name of the kafka Queue Source in the Sources Config.")
@click.option("--dry-run", is_flag=True,
              help="Report the topic and target Watched Directory without consuming.")
def materialize(config_path, source_name, dry_run):
    """Materialize one Kafka Queue Source into complete NDJSON Files (Drain mode)."""
    try:
        outcome = run_materialize(config_path, source_name, dry_run=dry_run)
    except (MaterializeError, CompanionError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if outcome.dry_run:
        click.echo(
            f"[dry-run] {outcome.source_name}: would drain topic "
            f"{outcome.topic!r} into {outcome.watched_directory}"
        )
        return
    if outcome.skipped:
        click.echo(f"{outcome.source_name}: no new records on {outcome.topic!r}; nothing promoted.")
        return
    click.echo(
        f"{outcome.source_name}: materialized {outcome.batch_count} Micro-batches "
        f"({outcome.record_count} records) from {outcome.topic!r}; "
        f"promoted {len(outcome.promoted)} files into {outcome.watched_directory}"
    )


if __name__ == "__main__":  # pragma: no cover
    materialize()
