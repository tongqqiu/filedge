"""Tests for the Authoring Validation service (#147) — the headless, non-mutating
compatibility check between a sample File and a Pipeline Config. Everything here
drives the service from Python alone; no UI, no Run, no Audit DB."""

from filedge.authoring_validation import (
    SCOPE_COLUMN_TOLERANCE,
    SCOPE_CONFIG_LOADING,
    SCOPE_FIELD_ENCRYPTION,
    SCOPE_PARSER,
    SCOPE_STRICT_MODE,
    SCOPE_WRITE_MODE,
    validate_authoring,
)
from filedge.config import (
    CdcConfig,
    ColumnMapping,
    EncryptConfig,
    PipelineConfig,
)


def _csv(tmp_path, body, name="sample.csv"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def _config(columns, *, fmt="csv", write_mode="append", cdc=None):
    return PipelineConfig(
        format=fmt,
        dest_table="t",
        columns=columns,
        write_mode=write_mode,
        cdc=cdc,
    )


def _col(source, type="string", *, required=True, dest=None, encrypt=None):
    return ColumnMapping(
        source=source,
        dest=dest or source,
        type=type,
        required=required,
        encrypt=encrypt,
    )


# --- happy path -------------------------------------------------------------


def test_compatible_file_and_config_reports_ok_across_the_scope(tmp_path):
    src = _csv(tmp_path, "id,name\n1,Alice\n2,Bob\n")
    cfg = _config([_col("id", "integer"), _col("name", "string")])

    report = validate_authoring(src, cfg)

    assert report.ok
    assert report.rows_checked == 2
    # Every Validation Scope dimension is enumerated.
    for scope in (
        SCOPE_CONFIG_LOADING,
        SCOPE_PARSER,
        SCOPE_COLUMN_TOLERANCE,
        SCOPE_STRICT_MODE,
        SCOPE_FIELD_ENCRYPTION,
        SCOPE_WRITE_MODE,
    ):
        assert report.findings_in(scope), f"missing finding for {scope}"


# --- Column Tolerance -------------------------------------------------------


def test_extra_source_columns_are_tolerated(tmp_path):
    src = _csv(tmp_path, "id,name,extra\n1,Alice,x\n")
    cfg = _config([_col("id", "integer")])  # 'name' and 'extra' undeclared

    report = validate_authoring(src, cfg)

    assert report.ok
    tolerance = report.findings_in(SCOPE_COLUMN_TOLERANCE)
    assert any("extra" in f.message and f.ok for f in tolerance)


def test_missing_required_column_is_a_failure(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    cfg = _config([_col("id", "integer"), _col("name", "string")])  # name absent

    report = validate_authoring(src, cfg)

    assert not report.ok
    failure = next(
        f for f in report.findings_in(SCOPE_COLUMN_TOLERANCE) if not f.ok
    )
    assert failure.column == "name"


def test_missing_optional_column_is_tolerated(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    cfg = _config([_col("id", "integer"), _col("name", "string", required=False)])

    report = validate_authoring(src, cfg)

    assert report.ok


# --- Strict Mode ------------------------------------------------------------


def test_strict_mode_coercion_failure_carries_row_level_context(tmp_path):
    src = _csv(tmp_path, "id\n1\nnot-a-number\n3\n")
    cfg = _config([_col("id", "integer")])

    report = validate_authoring(src, cfg)

    assert not report.ok
    strict = report.findings_in(SCOPE_STRICT_MODE)
    bad = [f for f in strict if not f.ok]
    assert len(bad) == 1
    assert bad[0].row_number == 2
    assert bad[0].column == "id"


def test_strict_mode_passes_for_clean_sample(tmp_path):
    src = _csv(tmp_path, "id\n1\n2\n")
    cfg = _config([_col("id", "integer")])

    report = validate_authoring(src, cfg)

    assert report.ok
    assert all(f.ok for f in report.findings_in(SCOPE_STRICT_MODE))


# --- Parser readability -----------------------------------------------------


def test_unreadable_sample_reports_parser_failure_and_stops(tmp_path):
    missing = str(tmp_path / "nope.csv")
    cfg = _config([_col("id", "integer")])

    report = validate_authoring(missing, cfg)

    assert not report.ok
    parser = report.findings_in(SCOPE_PARSER)
    assert parser and not parser[0].ok
    # We never reached Strict Mode because the File could not be read.
    assert not report.findings_in(SCOPE_STRICT_MODE)


# --- Write Mode -------------------------------------------------------------


def test_cdc_without_cdc_block_is_a_failure(tmp_path):
    src = _csv(tmp_path, "id,op,seq\n1,U,1\n")
    cfg = _config([_col("id", "integer"), _col("op"), _col("seq", "integer")],
                  write_mode="cdc", cdc=None)

    report = validate_authoring(src, cfg)

    assert not report.ok
    wm = report.findings_in(SCOPE_WRITE_MODE)
    assert wm and not wm[0].ok


def test_cdc_without_business_key_or_sequence_is_a_failure(tmp_path):
    src = _csv(tmp_path, "id,op,seq\n1,U,1\n")
    cdc = CdcConfig(keys=[], operation_column="op", sequence_by="", operations={})
    cfg = _config(
        [_col("id", "integer"), _col("op"), _col("seq", "integer")],
        write_mode="cdc",
        cdc=cdc,
    )

    report = validate_authoring(src, cfg)

    assert not report.ok
    failures = [f for f in report.findings_in(SCOPE_WRITE_MODE) if not f.ok]
    messages = " ".join(f.message for f in failures)
    assert "business key" in messages
    assert "sequence" in messages


def test_cdc_with_undeclared_key_is_a_failure(tmp_path):
    src = _csv(tmp_path, "id,op,seq\n1,U,1\n")
    cdc = CdcConfig(
        keys=["ghost"], operation_column="op", sequence_by="seq", operations={}
    )
    cfg = _config(
        [_col("id", "integer"), _col("op"), _col("seq", "integer")],
        write_mode="cdc",
        cdc=cdc,
    )

    report = validate_authoring(src, cfg)

    assert not report.ok
    assert any(
        "ghost" in f.message for f in report.findings_in(SCOPE_WRITE_MODE) if not f.ok
    )


def test_well_formed_cdc_passes_write_mode(tmp_path):
    src = _csv(tmp_path, "id,op,seq\n1,U,1\n")
    cdc = CdcConfig(
        keys=["id"], operation_column="op", sequence_by="seq", operations={}
    )
    cfg = _config(
        [_col("id", "integer"), _col("op"), _col("seq", "integer")],
        write_mode="cdc",
        cdc=cdc,
    )

    report = validate_authoring(src, cfg)

    assert report.ok
    assert all(f.ok for f in report.findings_in(SCOPE_WRITE_MODE))


# --- Field Encryption (shape only) ------------------------------------------


def test_field_encryption_is_reported_structurally_without_key_material(tmp_path):
    src = _csv(tmp_path, "email\nalice@example.com\n")
    enc = EncryptConfig(algorithm="aes-256-gcm", key="env:DEK")
    cfg = _config([_col("email", "string", encrypt=enc)])

    report = validate_authoring(src, cfg)

    assert report.ok
    fe = report.findings_in(SCOPE_FIELD_ENCRYPTION)
    assert fe and fe[0].ok
    # The placeholder reference is named but its key is never resolved/read.
    assert "key material not read" in fe[0].message


def test_no_field_encryption_still_enumerates_the_scope(tmp_path):
    src = _csv(tmp_path, "id\n1\n")
    cfg = _config([_col("id", "integer")])

    report = validate_authoring(src, cfg)

    assert report.findings_in(SCOPE_FIELD_ENCRYPTION)


# --- Regression: non-mutating, no Destination contact -----------------------


def test_service_never_runs_ingestion_or_touches_audit_db(tmp_path, monkeypatch):
    """The service must stay within the Validation Scope: no Run, no Audit DB."""
    import filedge.db as db
    import filedge.pipeline as pipeline

    def _boom(*args, **kwargs):
        raise AssertionError("Authoring Validation must not run ingestion / Audit DB")

    monkeypatch.setattr(pipeline, "run_pipeline", _boom)
    monkeypatch.setattr(db, "Database", _boom)
    monkeypatch.setattr(db, "insert_pending", _boom, raising=False)
    monkeypatch.setattr(db, "claim_processing", _boom, raising=False)

    src = _csv(tmp_path, "id,name\n1,Alice\n")
    cfg = _config([_col("id", "integer"), _col("name", "string")])

    report = validate_authoring(src, cfg)
    assert report.ok


def test_service_never_attempts_destination_reachability(tmp_path, monkeypatch):
    """No Connector is instantiated — Destination reachability is out of Scope."""
    import filedge.connectors as connectors

    def _boom(*args, **kwargs):
        raise AssertionError("Authoring Validation must not contact a Destination")

    monkeypatch.setattr(connectors, "get_connector", _boom)

    src = _csv(tmp_path, "id\n1\n")
    cfg = _config([_col("id", "integer")])

    report = validate_authoring(src, cfg)
    assert report.ok
