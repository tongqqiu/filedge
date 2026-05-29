"""Tests for the multi-Pipeline status fan-out (deep module, not the CLI).

These guard the external behavior #194 asks for: every Pipeline's summary comes
back keyed by its Registry id and in Registry order, and a single unresolvable or
unopenable Audit DB becomes an errored result while the other Pipelines still
report. The one-Audit-DB-per-Pipeline rule means each entry is backed by its own
SQLite Audit DB; the fan-out opens them independently.
"""

import yaml

from filedge.db import Database, create_audit_tables, insert_pending, mark_failed
from filedge.status_all import status_all


def _make_folder(workspace, folder):
    """Create a minimal Pipeline Folder with a pipeline.yaml so the Registry loads."""
    folder_path = workspace / folder
    folder_path.mkdir(parents=True)
    (folder_path / "pipeline.yaml").write_text("version: 1\n")
    return folder_path


def _seed_audit_db(url, *, failures=()):
    """Create the audit tables at ``url`` and optionally seed FAILED records."""
    db = Database(url)
    create_audit_tables(db)
    for filename, content_hash, error in failures:
        insert_pending(db, filename, content_hash)
        mark_failed(db, content_hash, error)
    db.commit()
    db.close()


def _write_registry(workspace, entries):
    """Write a pipeline-registry.yaml listing ``entries`` (dicts)."""
    (workspace / "pipeline-registry.yaml").write_text(
        yaml.safe_dump({"version": 1, "pipelines": entries}, sort_keys=False)
    )


def test_fans_out_summaries_keyed_by_id(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _make_folder(workspace, "pipelines/alpha")
    _make_folder(workspace, "pipelines/beta")

    db_alpha = f"sqlite:///{tmp_path / 'alpha.db'}"
    db_beta = f"sqlite:///{tmp_path / 'beta.db'}"
    _seed_audit_db(db_alpha)
    _seed_audit_db(db_beta, failures=[("broken.csv", "h-beta", "boom")])

    monkeypatch.setenv("ALPHA_DB", db_alpha)
    monkeypatch.setenv("BETA_DB", db_beta)
    _write_registry(
        workspace,
        [
            {
                "id": "alpha",
                "folder": "pipelines/alpha",
                "watched_directory": "/data/alpha",
                "audit_db": "env:ALPHA_DB",
                "audit_export": "/exports/alpha",
            },
            {
                "id": "beta",
                "folder": "pipelines/beta",
                "watched_directory": "/data/beta",
                "audit_db": "env:BETA_DB",
                "audit_export": "/exports/beta",
            },
        ],
    )

    results = status_all(str(workspace))

    # Registry order is preserved, both keyed by id, both summaries present.
    assert [r.id for r in results] == ["alpha", "beta"]
    assert all(r.error is None for r in results)
    assert results[0].summary["FAILED"] == 0
    assert results[1].summary["FAILED"] == 1
    assert results[1].summary["recent_failures"][0]["filename"] == "broken.csv"


def test_unset_placeholder_errors_one_entry_others_still_report(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _make_folder(workspace, "pipelines/alpha")
    _make_folder(workspace, "pipelines/beta")

    db_alpha = f"sqlite:///{tmp_path / 'alpha.db'}"
    _seed_audit_db(db_alpha)

    monkeypatch.setenv("ALPHA_DB", db_alpha)
    monkeypatch.delenv("BETA_DB", raising=False)  # beta's placeholder is unset
    _write_registry(
        workspace,
        [
            {
                "id": "alpha",
                "folder": "pipelines/alpha",
                "watched_directory": "/data/alpha",
                "audit_db": "env:ALPHA_DB",
                "audit_export": "/exports/alpha",
            },
            {
                "id": "beta",
                "folder": "pipelines/beta",
                "watched_directory": "/data/beta",
                "audit_db": "env:BETA_DB",
                "audit_export": "/exports/beta",
            },
        ],
    )

    results = status_all(str(workspace))

    by_id = {r.id: r for r in results}
    # Healthy Pipeline still returns a summary...
    assert by_id["alpha"].summary is not None
    assert by_id["alpha"].error is None
    # ...while the unset placeholder yields an errored result naming the variable.
    assert by_id["beta"].summary is None
    assert by_id["beta"].error is not None
    assert "BETA_DB" in by_id["beta"].error
