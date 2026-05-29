"""Tests for the Pipeline Folder writer + Authoring Runbook renderer + first-
Pipeline Registry creation (#148), per ADR-0017. Everything is driven from
Python alone; nothing is run, and no Audit DB is touched."""

import os

import pytest
import yaml

from filedge.authoring_draft import PipelineConfigDraft
from filedge.config import load_config
from filedge.pipeline_folder import (
    read_runbook_sample_file,
    slugify_pipeline_id,
    write_pipeline_folder,
)
from filedge.pipeline_registry import (
    REGISTRY_FILENAME,
    PipelineRegistry,
    RegistryEntry,
    RegistryError,
    add_entry,
    load_registry,
    parse_registry,
)


def _csv(tmp_path, body, name="sample.csv"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def _draft_config(tmp_path, dest_table="orders"):
    src = _csv(tmp_path, "id,name\n1,Alice\n2,Bob\n")
    draft = PipelineConfigDraft.from_sample(src, dest_table)
    return src, draft.to_config_dict()


# --- slug -------------------------------------------------------------------


def test_slugify_collapses_to_hyphenated_lowercase():
    assert slugify_pipeline_id("Daily_Orders") == "daily-orders"
    assert slugify_pipeline_id("  Customers!! ") == "customers"


def test_slugify_rejects_empty_result():
    with pytest.raises(ValueError):
        slugify_pipeline_id("!!!")


# --- first-Pipeline create path ---------------------------------------------


def test_first_pipeline_creates_folder_runbook_and_registry(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "Daily_Orders")

    result = write_pipeline_folder(workspace, config, sample_file=sample)

    assert result.pipeline_id == "daily-orders"
    assert result.folder == "pipelines/daily-orders"
    assert os.path.isfile(result.config_path)
    assert os.path.isfile(result.runbook_path)
    # The Registry was created lazily on this first Pipeline.
    assert os.path.isfile(os.path.join(workspace, REGISTRY_FILENAME))

    registry = load_registry(workspace)
    assert [e.id for e in registry.entries] == ["daily-orders"]
    entry = registry.entries[0]
    assert entry.folder == "pipelines/daily-orders"
    assert entry.audit_db == "env:DAILY_ORDERS_AUDIT_DB_URL"
    assert entry.watched_directory == "./landing/daily-orders"
    assert entry.audit_export == "./audit-exports/daily-orders"


def test_generated_pipeline_yaml_round_trips_through_loader(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    result = write_pipeline_folder(workspace, config, sample_file=sample)

    cfg = load_config(result.config_path)  # must not raise
    assert cfg.dest_table == "orders"
    assert cfg.format == "csv"
    assert [c.source for c in cfg.columns] == ["id", "name"]


def test_runbook_records_sample_path_and_suggested_commands(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    result = write_pipeline_folder(workspace, config, sample_file=sample)
    runbook = open(result.runbook_path).read()

    assert sample in runbook
    for cmd in ("filedge validate", "filedge healthcheck", "filedge run",
                "filedge export-audit"):
        assert cmd in runbook
    # The Audit DB appears only as a placeholder / shell reference, never a value.
    assert "env:ORDERS_AUDIT_DB_URL" in runbook


def test_runbook_records_confidence_tier_acknowledgements(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    result = write_pipeline_folder(
        workspace,
        config,
        sample_file=sample,
        confidence_acknowledgements=[
            {
                "source": "name",
                "dest": "customer_name",
                "confidence": "ambiguous",
                "evidence": "null_count=0, total_seen=2",
            }
        ],
    )
    runbook = open(result.runbook_path).read()

    assert "Source `name` -> destination `customer_name`" in runbook
    assert "accepted `ambiguous` Confidence Tier" in runbook
    assert "null_count=0, total_seen=2" in runbook


def test_out_override_sets_the_pipeline_id(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    result = write_pipeline_folder(workspace, config, sample_file=sample, out="EU Sales")

    assert result.pipeline_id == "eu-sales"
    assert os.path.isdir(os.path.join(workspace, "pipelines", "eu-sales"))


# --- Runbook is non-secret --------------------------------------------------


def test_runbook_never_leaks_environment_variable_values(tmp_path, monkeypatch):
    """Regression: a secret env value must never bleed into the Runbook."""
    secret = "super-secret-connection-string-9f3a"
    monkeypatch.setenv("ORDERS_AUDIT_DB_URL", secret)
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    result = write_pipeline_folder(workspace, config, sample_file=sample)
    runbook = open(result.runbook_path).read()
    registry = open(os.path.join(workspace, REGISTRY_FILENAME)).read()

    assert secret not in runbook
    assert secret not in registry


# --- additional-Pipeline append path ----------------------------------------


def test_second_pipeline_appends_without_merging_audit_dbs(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample_a, config_a = _draft_config(tmp_path, "orders")
    sample_b = _csv(tmp_path, "id,city\n1,NYC\n", name="cust.csv")
    config_b = PipelineConfigDraft.from_sample(sample_b, "customers").to_config_dict()

    write_pipeline_folder(workspace, config_a, sample_file=sample_a)
    write_pipeline_folder(workspace, config_b, sample_file=sample_b)

    registry = load_registry(workspace)
    assert [e.id for e in registry.entries] == ["orders", "customers"]
    # Each Pipeline keeps its own, distinct Audit DB placeholder.
    audit_dbs = {e.audit_db for e in registry.entries}
    assert len(audit_dbs) == 2


def test_duplicate_id_is_rejected(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    write_pipeline_folder(workspace, config, sample_file=sample)
    # Re-authoring the same id collides on the existing folder.
    with pytest.raises(ValueError):
        write_pipeline_folder(workspace, config, sample_file=sample)


# --- Registry validation (reject malformed entries) -------------------------


def test_registry_rejects_missing_field(tmp_path):
    data = {
        "version": 1,
        "pipelines": [{"id": "orders", "folder": "pipelines/orders"}],
    }
    with pytest.raises(RegistryError, match="missing required field"):
        parse_registry(data)


def test_registry_rejects_duplicate_id(tmp_path):
    entry = {
        "id": "orders",
        "folder": "pipelines/orders",
        "watched_directory": "./landing/orders",
        "audit_db": "env:A",
        "audit_export": "./x",
    }
    dup = dict(entry, audit_db="env:B")
    with pytest.raises(RegistryError, match="Duplicate Pipeline id"):
        parse_registry({"version": 1, "pipelines": [entry, dup]})


def test_registry_rejects_shared_audit_db(tmp_path):
    a = {
        "id": "orders",
        "folder": "pipelines/orders",
        "watched_directory": "./landing/orders",
        "audit_db": "env:SHARED",
        "audit_export": "./x",
    }
    b = dict(a, id="customers", folder="pipelines/customers")
    with pytest.raises(RegistryError, match="one Audit DB"):
        parse_registry({"version": 1, "pipelines": [a, b]})


def test_registry_rejects_literal_audit_db(tmp_path):
    entry = {
        "id": "orders",
        "folder": "pipelines/orders",
        "watched_directory": "./landing/orders",
        "audit_db": "postgresql://user:pw@host/db",
        "audit_export": "./x",
    }
    with pytest.raises(RegistryError, match="placeholder"):
        parse_registry({"version": 1, "pipelines": [entry]})


def test_registry_rejects_unsupported_version(tmp_path):
    with pytest.raises(RegistryError, match="version"):
        parse_registry({"version": 2, "pipelines": []})


def test_registry_rejects_missing_folder_on_workspace_load(tmp_path):
    workspace = str(tmp_path)
    registry = PipelineRegistry(
        entries=[
            RegistryEntry(
                id="orders",
                folder="pipelines/orders",  # never created on disk
                watched_directory="./landing/orders",
                audit_db="env:ORDERS",
                audit_export="./x",
            )
        ]
    )
    (tmp_path / REGISTRY_FILENAME).write_text(yaml.safe_dump(registry.to_dict()))
    with pytest.raises(RegistryError, match="does not exist"):
        load_registry(workspace)


def test_add_entry_rejects_shared_audit_db_before_writing(tmp_path):
    """A reused Audit DB placeholder is rejected and the file is left untouched."""
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")
    write_pipeline_folder(workspace, config, sample_file=sample)

    # Craft a second, real Pipeline Folder so the folder-existence check passes,
    # then try to register it with the first Pipeline's Audit DB placeholder.
    os.makedirs(os.path.join(workspace, "pipelines", "customers"))
    open(os.path.join(workspace, "pipelines", "customers", "pipeline.yaml"), "w").close()

    before = open(os.path.join(workspace, REGISTRY_FILENAME)).read()
    with pytest.raises(RegistryError, match="one Audit DB"):
        add_entry(
            workspace,
            RegistryEntry(
                id="customers",
                folder="pipelines/customers",
                watched_directory="./landing/customers",
                audit_db="env:ORDERS_AUDIT_DB_URL",  # already owned by 'orders'
                audit_export="./audit-exports/customers",
            ),
        )
    after = open(os.path.join(workspace, REGISTRY_FILENAME)).read()
    assert before == after


def test_registry_rejects_non_mapping_document():
    with pytest.raises(RegistryError, match="must be a mapping"):
        parse_registry(["not", "a", "mapping"])


def test_registry_rejects_non_list_pipelines():
    with pytest.raises(RegistryError, match="pipelines"):
        parse_registry({"version": 1, "pipelines": "nope"})


def test_registry_rejects_non_mapping_entry():
    with pytest.raises(RegistryError, match="entry must be a mapping"):
        parse_registry({"version": 1, "pipelines": ["nope"]})


def test_registry_accepts_secrets_placeholder_audit_db():
    entry = {
        "id": "orders",
        "folder": "pipelines/orders",
        "watched_directory": "./landing/orders",
        "audit_db": "secrets:/run/secrets/orders-audit-db",
        "audit_export": "./x",
    }
    registry = parse_registry({"version": 1, "pipelines": [entry]})
    assert registry.entries[0].audit_db == "secrets:/run/secrets/orders-audit-db"


def test_registry_rejects_folder_without_pipeline_yaml_on_load(tmp_path):
    workspace = str(tmp_path)
    os.makedirs(os.path.join(workspace, "pipelines", "orders"))  # folder, no yaml
    registry = PipelineRegistry(
        entries=[
            RegistryEntry(
                id="orders",
                folder="pipelines/orders",
                watched_directory="./landing/orders",
                audit_db="env:ORDERS",
                audit_export="./x",
            )
        ]
    )
    (tmp_path / REGISTRY_FILENAME).write_text(yaml.safe_dump(registry.to_dict()))
    with pytest.raises(RegistryError, match="lacks pipeline.yaml"):
        load_registry(workspace)


def test_load_registry_raises_when_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_registry(str(tmp_path))


def test_secrets_audit_db_renders_in_runbook_without_value(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path, "orders")

    result = write_pipeline_folder(
        workspace,
        config,
        sample_file=sample,
        audit_db="secrets:/run/secrets/orders-audit-db",
    )
    runbook = open(result.runbook_path).read()
    assert "secrets:/run/secrets/orders-audit-db" in runbook


# --- re-author save-back (#173) ---------------------------------------------


def test_runbook_records_injectable_authored_at_timestamp(tmp_path):
    from datetime import datetime

    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path)
    stamp = datetime(2026, 5, 29, 14, 30, 0)

    result = write_pipeline_folder(
        workspace, config, sample_file=sample, authored_at=stamp
    )

    runbook = open(result.runbook_path).read()
    assert "Authored at: `2026-05-29T14:30:00`" in runbook


def test_overwrite_rewrites_folder_and_preserves_registry(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path)
    write_pipeline_folder(workspace, config, sample_file=sample)
    registry_before = load_registry(workspace).to_dict()

    # Flip a column type and save back over the existing Folder.
    config["columns"][0]["type"] = "string"
    result = write_pipeline_folder(
        workspace,
        config,
        sample_file=sample,
        watched_directory="./landing/orders",
        audit_db="env:ORDERS_AUDIT_DB_URL",
        audit_export="./audit-exports/orders",
        overwrite=True,
    )

    reloaded = load_config(result.config_path)
    assert reloaded.columns[0].type == "string"
    # The Registry is the durable record; overwrite leaves it byte-for-byte.
    assert load_registry(workspace).to_dict() == registry_before


def test_write_pipeline_folder_without_overwrite_still_rejects_existing(tmp_path):
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace)
    sample, config = _draft_config(tmp_path)
    write_pipeline_folder(workspace, config, sample_file=sample)
    with pytest.raises(ValueError, match="already exists"):
        write_pipeline_folder(workspace, config, sample_file=sample)


def test_read_runbook_sample_file_round_trips_and_handles_absence():
    runbook = "## Sample File\n\nAuthored from sample File: `/data/people.csv`\n"
    assert read_runbook_sample_file(runbook) == "/data/people.csv"
    assert read_runbook_sample_file("no sample line here") is None
