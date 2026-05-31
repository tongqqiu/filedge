"""Tests for the Pipeline Config Draft (#146) — the headless, editable core of
the Authoring Workflow. Everything here drives the draft through its public
interface from Python alone; no UI is involved."""

import pytest

from filedge.authoring_draft import (
    ColumnDraft,
    EncryptDraft,
    HashDraft,
    PipelineConfigDraft,
    draft_from_config,
)
from filedge.config import config_from_dict, load_config


def _csv(tmp_path, body, name="sample.csv"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_from_sample_exposes_inferred_columns_with_confidence(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n2,Bob\n3,Carol\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    by_name = {c.source: c for c in draft.columns}
    assert set(by_name) == {"id", "name"}
    assert by_name["id"].type == "integer"
    assert by_name["name"].type == "string"
    # Confidence Tier rides along as read-only evidence.
    assert by_name["id"].confidence == "high"
    # dest defaults to the source name; columns are required by default.
    assert by_name["id"].dest == "id"
    assert by_name["id"].required is True


def test_from_sample_surfaces_low_and_ambiguous_tiers(tmp_path):
    # `amount` has a null -> low; `blank` is entirely empty -> ambiguous.
    src = _csv(tmp_path, "amount,blank\n10,\n,\n20,\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    by_name = {c.source: c for c in draft.columns}
    assert by_name["amount"].confidence == "low"
    assert by_name["blank"].confidence == "ambiguous"


def test_edit_column_changes_authored_fields(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    draft.edit_column(
        "name", new_source="name", dest="full_name", type="string", required=False
    )
    draft.edit_column("id", dest="person_id", start=1, width=3)

    by_dest = {c.dest: c for c in draft.columns}
    assert by_dest["full_name"].required is False
    assert by_dest["person_id"].source == "id"
    assert by_dest["person_id"].start == 1
    assert by_dest["person_id"].width == 3


def test_edit_column_rejects_invalid_type_without_mutating(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    with pytest.raises(ValueError):
        draft.edit_column("id", type="int64")
    # The rejected edit left the column unchanged.
    assert draft.column("id").type == "integer"


def test_edit_unknown_column_raises(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    with pytest.raises(KeyError):
        draft.edit_column("nope", dest="x")


def test_to_config_round_trips_via_config_from_dict(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n2,Bob\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    draft.edit_column("name", required=False)

    cfg = draft.to_config()  # config_from_dict under the hood — must not raise
    assert cfg.dest_table == "people"
    assert cfg.format == "csv"
    assert cfg.write_mode == "append"
    by_source = {c.source: c for c in cfg.columns}
    assert by_source["id"].type == "integer"
    assert by_source["name"].required is False


def test_write_mode_and_cdc_settings_round_trip(tmp_path):
    src = _csv(tmp_path, "id,op,updated_at\n1,insert,2026-01-01T00:00:00Z\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    draft.choose_write_mode("cdc")
    draft.set_cdc_settings(business_keys=["id"], sequence_by="updated_at")
    cfg = draft.to_config()

    assert cfg.write_mode == "cdc"
    assert cfg.cdc.keys == ["id"]
    assert cfg.cdc.operation_column == "op"
    assert cfg.cdc.sequence_by == "updated_at"
    assert cfg.cdc.operations["delete"] == ["d", "delete"]


def test_write_mode_rejects_unknown_mode(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    with pytest.raises(ValueError, match="Write Mode"):
        draft.choose_write_mode("merge")


def test_to_config_dict_round_trips_through_file_loader(tmp_path):
    import yaml

    src = _csv(tmp_path, "id,name\n1,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    cfg_path = tmp_path / "pipeline.yaml"
    cfg_path.write_text(yaml.safe_dump(draft.to_config_dict()))
    cfg = load_config(str(cfg_path))  # the exact path the Operator CLI uses
    assert cfg.dest_table == "people"
    assert [c.source for c in cfg.columns] == ["id", "name"]


def test_ndjson_sample_surfaces_nested_object_warning(tmp_path):
    src = _csv(
        tmp_path,
        '{"id": 1, "profile": {"tier": "gold"}}\n',
        name="data.ndjson",
    )

    draft = PipelineConfigDraft.from_sample(src, "events", fmt="ndjson")

    profile = draft.column("profile")
    assert profile.type == "string"
    assert profile.confidence == "ambiguous"
    assert any("nested object" in note for note in profile.notes)


def test_fixed_width_requires_manual_layout(tmp_path):
    src = _csv(tmp_path, "001Alice\n", name="data.txt")
    with pytest.raises(ValueError, match="Fixed-Width Layout"):
        PipelineConfigDraft.from_sample(src, "people", fmt="fixed_width")


def test_fixed_width_layout_round_trips_through_config_loader():
    draft = PipelineConfigDraft.from_fixed_width_layout(
        "people",
        [
            ColumnDraft("id", "id", "integer", start=1, width=3),
            ColumnDraft("name", "name", "string", start=4, width=5),
        ],
    )

    cfg = draft.to_config()

    assert cfg.format == "fixed_width"
    assert cfg.columns[0].start == 1
    assert cfg.columns[1].width == 5


def test_fixed_width_layout_surfaces_config_load_validation_errors():
    draft = PipelineConfigDraft.from_fixed_width_layout(
        "people",
        [
            ColumnDraft("id", "id", "integer", start=1, width=3),
            ColumnDraft("name", "name", "string", start=3, width=5),
        ],
    )

    with pytest.raises(ValueError, match="overlap"):
        draft.to_config()


def test_add_fixed_width_column_rejects_non_fixed_width_draft(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "people")

    with pytest.raises(ValueError, match="fixed_width"):
        draft.add_fixed_width_column(
            source="id",
            dest="id",
            type="integer",
            start=1,
            width=3,
        )


def test_excel_draft_records_selected_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["id", "name"])
    ws.append([1, "Alice"])
    wb.create_sheet("Other").append(["ignored"])
    wb.save(path)

    draft = PipelineConfigDraft.from_sample(
        str(path), "orders", fmt="excel", sheet="Orders"
    )

    assert draft.to_config_dict()["excel"] == {"sheet": "Orders"}
    assert draft.to_config().excel.sheet == "Orders"


def test_config_from_dict_matches_load_config(tmp_path):
    # Parity guard for the load_config refactor: dict-in == file-in.
    data = {
        "format": "csv",
        "dest_table": "t",
        "columns": [{"source": "id", "dest": "id", "type": "integer"}],
    }
    import yaml

    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    assert config_from_dict(data) == load_config(str(p))


def test_column_draft_defaults():
    c = ColumnDraft(source="a", dest="a", type="string")
    assert c.required is True
    assert c.confidence == "high"
    assert c.notes == []
    assert c.encrypt is None
    assert c.hash is None


def test_set_field_encryption_round_trips_through_config(tmp_path):
    src = _csv(tmp_path, "ssn,name\n123-45-6789,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    draft.edit_column("ssn", type="string")

    draft.set_field_encryption(
        "ssn",
        encrypt=EncryptDraft(key="env:SSN_ENC_KEY"),
        hash=HashDraft(key="env:SSN_HASH_KEY"),
    )
    cfg = draft.to_config()

    ssn = next(c for c in cfg.columns if c.dest == "ssn")
    assert ssn.encrypt is not None
    assert ssn.encrypt.algorithm == "aes-256-gcm"
    assert ssn.encrypt.key == "env:SSN_ENC_KEY"
    assert ssn.hash is not None
    assert ssn.hash.algorithm == "hmac-sha256"
    assert ssn.hash.key == "env:SSN_HASH_KEY"


def test_set_field_encryption_accepts_neither_one_or_both(tmp_path):
    src = _csv(tmp_path, "ssn,phone,name\n1,2,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    draft.edit_column("ssn", type="string")

    draft.set_field_encryption("ssn", encrypt=EncryptDraft(key="env:K1"))
    draft.set_field_encryption("phone", hash=HashDraft(key="env:K2"))

    assert draft.column_by_dest("ssn").encrypt is not None
    assert draft.column_by_dest("ssn").hash is None
    assert draft.column_by_dest("phone").encrypt is None
    assert draft.column_by_dest("phone").hash is not None
    assert draft.column_by_dest("name").encrypt is None
    assert draft.column_by_dest("name").hash is None


def test_clear_field_encryption_removes_blocks(tmp_path):
    src = _csv(tmp_path, "ssn\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    draft.edit_column("ssn", type="string")
    draft.set_field_encryption(
        "ssn",
        encrypt=EncryptDraft(key="env:K"),
        hash=HashDraft(key="env:H"),
    )

    draft.clear_field_encryption("ssn", encrypt=True)
    assert draft.column_by_dest("ssn").encrypt is None
    assert draft.column_by_dest("ssn").hash is not None

    draft.clear_field_encryption("ssn", hash=True)
    assert draft.column_by_dest("ssn").hash is None


def test_duplicate_column_supports_two_destinations_from_one_source(tmp_path):
    src = _csv(tmp_path, "ssn,name\n123,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    draft.edit_column("ssn", type="string")

    draft.duplicate_column("ssn", new_dest="ssn_hash")
    draft.set_field_encryption("ssn", encrypt=EncryptDraft(key="env:E"))
    draft.set_field_encryption("ssn_hash", hash=HashDraft(key="env:H"))

    cfg = draft.to_config()
    ssn_dests = [c for c in cfg.columns if c.source == "ssn"]
    assert {c.dest for c in ssn_dests} == {"ssn", "ssn_hash"}
    enc_col = next(c for c in ssn_dests if c.dest == "ssn")
    hash_col = next(c for c in ssn_dests if c.dest == "ssn_hash")
    assert enc_col.encrypt is not None and enc_col.hash is None
    assert hash_col.hash is not None and hash_col.encrypt is None


def test_duplicate_column_rejects_existing_dest(tmp_path):
    src = _csv(tmp_path, "ssn,name\n1,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    with pytest.raises(ValueError, match="already exists"):
        draft.duplicate_column("ssn", new_dest="name")


def test_field_encryption_shape_failure_surfaced_via_config_loader(tmp_path):
    # Authoring Validation reuses the loader's structural check; an encrypt on
    # a non-string column is rejected at to_config() time.
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    draft.set_field_encryption("id", encrypt=EncryptDraft(key="env:K"))
    with pytest.raises(ValueError, match="type: string"):
        draft.to_config()


def test_field_encryption_key_reference_must_be_a_placeholder(tmp_path):
    src = _csv(tmp_path, "ssn\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    draft.edit_column("ssn", type="string")
    draft.set_field_encryption("ssn", encrypt=EncryptDraft(key="literal-secret"))
    with pytest.raises(ValueError, match="env:NAME or secrets:/"):
        draft.to_config()


def test_column_by_dest_raises_for_unknown_dest(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    draft = PipelineConfigDraft.from_sample(src, "t")
    with pytest.raises(KeyError):
        draft.column_by_dest("nope")


def test_field_encryption_columns_lists_only_declared(tmp_path):
    src = _csv(tmp_path, "ssn,name\n1,Alice\n")
    draft = PipelineConfigDraft.from_sample(src, "people")
    draft.edit_column("ssn", type="string")
    draft.set_field_encryption("ssn", encrypt=EncryptDraft(key="env:K"))
    assert [c.dest for c in draft.field_encryption_columns()] == ["ssn"]


# ---------------------------------------------------------------------------
# draft_from_config — load an existing PipelineConfig back into a Draft (#172)
# ---------------------------------------------------------------------------


def _minimal_config():
    """Minimal CSV/append/sqlite config — the supported shape for the loader."""
    return config_from_dict(
        {
            "format": "csv",
            "dest_table": "orders",
            "write_mode": "append",
            "connector": {"type": "sqlite", "path": "audit.db"},
            "columns": [
                {"source": "id", "dest": "order_id", "type": "integer", "required": True},
                {"source": "amount", "dest": "amount", "type": "float", "required": False},
            ],
        }
    )


def test_draft_from_config_tracer_bullet():
    """Loaded Draft exposes the same column source/dest/type/required as the config."""
    cfg = _minimal_config()
    draft = draft_from_config(cfg)

    by_source = {c.source: c for c in draft.columns}
    assert set(by_source) == {"id", "amount"}
    assert by_source["id"].dest == "order_id"
    assert by_source["id"].type == "integer"
    assert by_source["id"].required is True
    assert by_source["amount"].required is False


def test_draft_from_config_loads_field_encryption_encrypt_block():
    cfg = config_from_dict(
        {
            "format": "csv",
            "dest_table": "t",
            "connector": {"type": "sqlite"},
            "columns": [
                {
                    "source": "ssn",
                    "dest": "ssn",
                    "type": "string",
                    "encrypt": {"algorithm": "aes-256-gcm", "key": "env:K"},
                }
            ],
        }
    )
    draft = draft_from_config(cfg)

    ssn = draft.column_by_dest("ssn")
    assert ssn.encrypt is not None
    assert ssn.encrypt.algorithm == "aes-256-gcm"
    assert ssn.encrypt.key == "env:K"
    assert ssn.hash is None


def test_draft_from_config_loads_field_encryption_hash_block():
    cfg = config_from_dict(
        {
            "format": "csv",
            "dest_table": "t",
            "connector": {"type": "sqlite"},
            "columns": [
                {
                    "source": "ssn",
                    "dest": "ssn",
                    "type": "string",
                    "hash": {"algorithm": "hmac-sha256", "key": "env:H"},
                }
            ],
        }
    )
    draft = draft_from_config(cfg)

    ssn = draft.column_by_dest("ssn")
    assert ssn.encrypt is None
    assert ssn.hash is not None
    assert ssn.hash.algorithm == "hmac-sha256"
    assert ssn.hash.key == "env:H"


def test_draft_from_config_rejects_unsupported_format():
    # No Parser ships for `avro` (ADR-0012 explicitly deprioritised it).
    cfg_dict = {
        "format": "avro",
        "dest_table": "t",
        "connector": {"type": "sqlite"},
        "columns": [{"source": "id", "dest": "id", "type": "integer"}],
    }
    # config_from_dict accepts the dict (Parser dispatch happens elsewhere); the
    # loader is the gatekeeper for the re-author flow.
    cfg = config_from_dict(cfg_dict)
    with pytest.raises(ValueError, match="avro"):
        draft_from_config(cfg)


def test_draft_from_config_rejects_unknown_connector():
    cfg = config_from_dict(
        {
            "format": "csv",
            "dest_table": "t",
            "connector": {"type": "redshift"},
            "columns": [{"source": "id", "dest": "id", "type": "integer"}],
        }
    )
    with pytest.raises(ValueError, match="redshift"):
        draft_from_config(cfg)


def test_draft_from_config_rejects_unknown_write_mode():
    cfg_dict = {
        "format": "csv",
        "dest_table": "t",
        "write_mode": "append",
        "connector": {"type": "sqlite"},
        "columns": [{"source": "id", "dest": "id", "type": "integer"}],
    }
    cfg = config_from_dict(cfg_dict)
    # Forge an unknown write mode after loading (config_from_dict does not gate
    # the enum, but the loader must).
    cfg.write_mode = "merge"
    with pytest.raises(ValueError, match="merge"):
        draft_from_config(cfg)


def test_draft_from_config_loaded_confidence_sentinel():
    """Loaded columns carry confidence='loaded', not 'high' or any inference tier."""
    cfg = _minimal_config()
    draft = draft_from_config(cfg)
    for col in draft.columns:
        assert col.confidence == "loaded"


def test_draft_from_config_round_trip_identity():
    """Draft → to_config_dict() equals the dict that produced the original config."""
    src_dict = {
        "format": "csv",
        "dest_table": "orders",
        "write_mode": "append",
        "connector": {"type": "sqlite", "path": "audit.db"},
        "columns": [
            {"source": "id", "dest": "order_id", "type": "integer", "required": True},
            {"source": "name", "dest": "full_name", "type": "string", "required": True},
            {"source": "score", "dest": "score", "type": "float", "required": False},
            {"source": "active", "dest": "active", "type": "boolean", "required": False},
        ],
    }
    cfg = config_from_dict(src_dict)
    emitted = draft_from_config(cfg).to_config_dict()

    assert emitted["format"] == src_dict["format"]
    assert emitted["dest_table"] == src_dict["dest_table"]
    assert emitted["write_mode"] == src_dict["write_mode"]
    assert emitted["connector"]["type"] == "sqlite"
    assert emitted["connector"]["path"] == "audit.db"
    emitted_cols = {c["source"]: c for c in emitted["columns"]}
    for orig in src_dict["columns"]:
        em = emitted_cols[orig["source"]]
        assert em["dest"] == orig["dest"]
        assert em["type"] == orig["type"]
        assert em["required"] == orig["required"]


def test_draft_from_config_round_trips_mixed_field_encryption_columns():
    src_dict = {
        "format": "csv",
        "dest_table": "people",
        "write_mode": "append",
        "connector": {"type": "sqlite", "url": "sqlite:///people.db"},
        "columns": [
            {"source": "id", "dest": "id", "type": "integer", "required": True},
            {
                "source": "ssn",
                "dest": "ssn_ciphertext",
                "type": "string",
                "required": True,
                "encrypt": {"algorithm": "aes-256-gcm", "key": "env:SSN_ENC_KEY"},
            },
            {
                "source": "email",
                "dest": "email_token",
                "type": "string",
                "required": False,
                "hash": {"algorithm": "hmac-sha256", "key": "env:EMAIL_HASH_KEY"},
            },
            {
                "source": "phone",
                "dest": "phone_protected",
                "type": "string",
                "required": True,
                "encrypt": {"algorithm": "aes-256-gcm", "key": "env:PHONE_ENC_KEY"},
                "hash": {"algorithm": "hmac-sha256", "key": "env:PHONE_HASH_KEY"},
            },
        ],
    }

    emitted = draft_from_config(config_from_dict(src_dict)).to_config_dict()

    assert emitted == src_dict


# ---------------------------------------------------------------------------
# Non-CSV format round-trip (#177)
# ---------------------------------------------------------------------------


def _baseline_columns():
    return [
        {"source": "id", "dest": "id", "type": "integer", "required": True},
        {"source": "name", "dest": "name", "type": "string", "required": False},
    ]


@pytest.mark.parametrize("fmt", ["ndjson", "parquet"])
def test_draft_from_config_round_trips_simple_formats(fmt):
    src_dict = {
        "format": fmt,
        "dest_table": "events",
        "write_mode": "append",
        "connector": {"type": "sqlite", "path": "audit.db"},
        "columns": _baseline_columns(),
    }
    emitted = draft_from_config(config_from_dict(src_dict)).to_config_dict()
    assert emitted == src_dict


def test_draft_from_config_round_trips_excel_with_sheet_selector():
    src_dict = {
        "format": "excel",
        "dest_table": "orders",
        "write_mode": "append",
        "connector": {"type": "sqlite", "path": "audit.db"},
        "excel": {"sheet": "Orders"},
        "columns": _baseline_columns(),
    }
    draft = draft_from_config(config_from_dict(src_dict))
    assert draft.sheet == "Orders"
    assert draft.to_config_dict() == src_dict


def test_draft_from_config_excel_sheet_editable_on_loaded_draft():
    src_dict = {
        "format": "excel",
        "dest_table": "orders",
        "write_mode": "append",
        "connector": {"type": "sqlite"},
        "excel": {"sheet": "Sheet1"},
        "columns": _baseline_columns(),
    }
    draft = draft_from_config(config_from_dict(src_dict))
    draft.sheet = "Orders"
    assert draft.to_config_dict()["excel"] == {"sheet": "Orders"}


def test_draft_from_config_round_trips_fixed_width_layout():
    src_dict = {
        "format": "fixed_width",
        "dest_table": "people",
        "write_mode": "append",
        "connector": {"type": "sqlite", "path": "audit.db"},
        "columns": [
            {"source": "id", "dest": "id", "type": "integer", "required": True, "start": 1, "width": 3},
            {"source": "name", "dest": "name", "type": "string", "required": True, "start": 4, "width": 5},
        ],
    }
    draft = draft_from_config(config_from_dict(src_dict))
    assert draft.column("id").start == 1
    assert draft.column("name").width == 5
    assert draft.to_config_dict() == src_dict


# ---------------------------------------------------------------------------
# Non-sqlite Connectors + non-append Write Modes round-trip (#178)
# ---------------------------------------------------------------------------


_NON_SQLITE_CONNECTOR_CASES = [
    pytest.param(
        {"type": "postgres", "url": "env:DATABASE_URL"},
        id="postgres",
    ),
    pytest.param(
        {"type": "bigquery", "project": "my-project", "dataset": "analytics"},
        id="bigquery",
    ),
    pytest.param(
        {
            "type": "databricks",
            "server_hostname": "dbc.example.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "catalog": "main",
            "schema": "raw",
        },
        id="databricks",
    ),
    pytest.param(
        {"type": "duckdb", "path": "./analytics.duckdb"},
        id="duckdb",
    ),
]


@pytest.mark.parametrize("connector", _NON_SQLITE_CONNECTOR_CASES)
def test_draft_from_config_round_trips_all_connectors(connector):
    src_dict = {
        "format": "csv",
        "dest_table": "orders",
        "write_mode": "append",
        "connector": connector,
        "columns": _baseline_columns(),
    }
    cfg = config_from_dict(src_dict)
    draft = draft_from_config(cfg)
    assert draft.connector_type == connector["type"]
    assert draft.to_config_dict() == src_dict


def test_draft_from_config_round_trips_truncate_write_mode():
    src_dict = {
        "format": "csv",
        "dest_table": "orders",
        "write_mode": "truncate",
        "connector": {"type": "sqlite", "url": "sqlite:///orders.db"},
        "columns": _baseline_columns(),
    }
    draft = draft_from_config(config_from_dict(src_dict))
    assert draft.write_mode == "truncate"
    assert draft.to_config_dict() == src_dict


def test_draft_from_config_round_trips_cdc_write_mode_with_all_fields():
    src_dict = {
        "format": "csv",
        "dest_table": "people",
        "write_mode": "cdc",
        "connector": {"type": "postgres", "url": "env:DATABASE_URL"},
        "columns": [
            {"source": "id", "dest": "id", "type": "integer", "required": True},
            {"source": "op", "dest": "op", "type": "string", "required": True},
            {
                "source": "updated_at",
                "dest": "updated_at",
                "type": "timestamp",
                "required": True,
            },
            {"source": "name", "dest": "name", "type": "string", "required": False},
        ],
        "cdc": {
            "keys": ["id"],
            "operation_column": "op",
            "sequence_by": "updated_at",
            "operations": {
                "insert": ["c", "insert"],
                "update": ["u", "update"],
                "delete": ["d", "delete"],
            },
        },
    }
    draft = draft_from_config(config_from_dict(src_dict))
    assert draft.cdc_keys == ["id"]
    assert draft.cdc_sequence_by == "updated_at"
    assert draft.cdc_operation_column == "op"
    assert draft.to_config_dict() == src_dict


def test_draft_from_config_round_trips_custom_cdc_operations_map():
    # Operations map values are loaded verbatim, not silently replaced by the
    # built-in default.
    src_dict = {
        "format": "csv",
        "dest_table": "people",
        "write_mode": "cdc",
        "connector": {"type": "sqlite"},
        "columns": [
            {"source": "id", "dest": "id", "type": "integer", "required": True},
            {"source": "action", "dest": "action", "type": "string", "required": True},
            {"source": "seq", "dest": "seq", "type": "integer", "required": True},
        ],
        "cdc": {
            "keys": ["id"],
            "operation_column": "action",
            "sequence_by": "seq",
            "operations": {
                "insert": ["I"],
                "update": ["U"],
                "delete": ["D", "X"],
            },
        },
    }
    draft = draft_from_config(config_from_dict(src_dict))
    assert draft.cdc_operations["delete"] == ["D", "X"]
    assert draft.to_config_dict() == src_dict


def test_draft_from_config_does_not_read_credential_env_vars(monkeypatch):
    # Re-author flow must not read or prompt for credentials — verified by
    # asserting no credential env var is touched even when the loaded
    # Connector declares one.
    read_env_vars: list[str] = []
    original_getenv = __import__("os").environ.get

    def tracking_get(key, default=None):
        read_env_vars.append(key)
        return original_getenv(key, default)

    monkeypatch.setattr("os.environ", _SpyMapping(read_env_vars, dict(__import__("os").environ)))

    src_dict = {
        "format": "csv",
        "dest_table": "orders",
        "write_mode": "append",
        "connector": {
            "type": "bigquery",
            "project": "my-project",
            "dataset": "analytics",
        },
        "columns": _baseline_columns(),
    }
    draft = draft_from_config(config_from_dict(src_dict))
    draft.to_config_dict()
    # Credentials live behind these placeholders for each non-sqlite Connector;
    # none of them should have been read.
    forbidden = {
        "DATABASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "DATABRICKS_TOKEN",
    }
    assert not (forbidden & set(read_env_vars)), (
        f"Re-author read credential env vars: {forbidden & set(read_env_vars)}"
    )


class _SpyMapping(dict):
    """A dict subclass that records every key lookup via __getitem__/get."""

    def __init__(self, log, base):
        super().__init__(base)
        self._log = log

    def __getitem__(self, key):
        self._log.append(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._log.append(key)
        return super().get(key, default)


def test_draft_from_config_fixed_width_layout_validation_still_fires():
    # Overlap is rejected at config_from_dict time, before the loader sees it.
    with pytest.raises(ValueError, match="overlap"):
        config_from_dict(
            {
                "format": "fixed_width",
                "dest_table": "people",
                "connector": {"type": "sqlite"},
                "columns": [
                    {"source": "id", "dest": "id", "type": "integer", "start": 1, "width": 3},
                    {"source": "name", "dest": "name", "type": "string", "start": 3, "width": 5},
                ],
            }
        )
