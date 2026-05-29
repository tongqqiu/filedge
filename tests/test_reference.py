"""Tests for the shared env:/secrets: reference resolver.

These guard the string front door used by the Pipeline Registry's `audit_db`
resolution, and that extracting the seam did not regress Field Encryption key
resolution (which now sits on the same parsing).
"""

import base64

import pytest

from filedge.key_resolver import KeyResolutionError, resolve_key
from filedge.reference import ReferenceError, read_secret, resolve_reference


def test_resolve_env_reference_returns_value_verbatim(monkeypatch):
    monkeypatch.setenv("AUDIT_DB_URL", "postgresql://user@host/db")

    assert (
        resolve_reference("env:AUDIT_DB_URL", usage="audit_db")
        == "postgresql://user@host/db"
    )


def test_resolve_secrets_reference_reads_file_and_strips_trailing_newline(tmp_path):
    path = tmp_path / "audit_db_url"
    path.write_text("sqlite:///audit.db\n")

    assert (
        resolve_reference(f"secrets:{path}", usage="audit_db") == "sqlite:///audit.db"
    )


def test_resolve_env_reference_unset_names_the_variable(monkeypatch):
    monkeypatch.delenv("AUDIT_DB_URL", raising=False)

    with pytest.raises(ReferenceError, match="AUDIT_DB_URL"):
        resolve_reference("env:AUDIT_DB_URL", usage="audit_db")


def test_resolve_secrets_reference_missing_names_the_path(tmp_path):
    missing = tmp_path / "missing"

    with pytest.raises(ReferenceError, match="not found") as exc:
        resolve_reference(f"secrets:{missing}", usage="audit_db")
    assert str(missing) in str(exc.value)


def test_resolve_env_reference_empty_name_rejected():
    with pytest.raises(ReferenceError, match="variable name"):
        resolve_reference("env:", usage="audit_db")


def test_resolve_reference_unknown_scheme_rejected():
    with pytest.raises(ReferenceError, match="env:NAME or secrets:"):
        resolve_reference("postgresql://literal/connection", usage="audit_db")


def test_resolve_secrets_reference_without_leading_slash_rejected():
    # `secrets:` must be followed by an absolute path; a relative form is not a
    # recognized scheme and is rejected at classification.
    with pytest.raises(ReferenceError, match="env:NAME or secrets:"):
        resolve_reference("secrets:relative/path", usage="audit_db")


def test_read_secret_relative_path_rejected():
    with pytest.raises(ReferenceError, match="absolute path"):
        read_secret("relative/path", usage="audit_db")


# --- guards that the shared seam did not regress key resolution ---


def test_key_resolution_still_decodes_env_base64(monkeypatch):
    key = b"a" * 32
    monkeypatch.setenv("DATA_KEY", base64.b64encode(key).decode("ascii"))

    assert resolve_key("env:DATA_KEY", usage="encrypt") == key


def test_key_resolution_still_reads_binary_secret_file(tmp_path):
    path = tmp_path / "data_key"
    path.write_bytes(b"b" * 32 + b"\n")

    assert resolve_key(f"secrets:{path}", usage="encrypt") == b"b" * 32


def test_key_resolution_unknown_scheme_still_raises_key_error():
    with pytest.raises(KeyResolutionError, match="env:NAME or secrets:"):
        resolve_key("literal-key-material", usage="encrypt")
