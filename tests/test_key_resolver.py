import base64

import pytest

from filedge.key_resolver import KeyResolutionError, resolve_key


def test_resolve_env_key_base64_decodes_material(monkeypatch):
    key = b"a" * 32
    monkeypatch.setenv("DATA_KEY", base64.b64encode(key).decode("ascii"))

    assert resolve_key("env:DATA_KEY", usage="encrypt") == key


def test_resolve_env_key_missing_raises(monkeypatch):
    monkeypatch.delenv("DATA_KEY", raising=False)

    with pytest.raises(KeyResolutionError, match="DATA_KEY"):
        resolve_key("env:DATA_KEY", usage="encrypt")


def test_resolve_env_key_malformed_base64_raises(monkeypatch):
    monkeypatch.setenv("DATA_KEY", "not base64")

    with pytest.raises(KeyResolutionError, match="base64"):
        resolve_key("env:DATA_KEY", usage="encrypt")


def test_resolve_encrypt_key_requires_32_bytes(monkeypatch):
    monkeypatch.setenv("DATA_KEY", base64.b64encode(b"short").decode("ascii"))

    with pytest.raises(KeyResolutionError, match="32 bytes"):
        resolve_key("env:DATA_KEY", usage="encrypt")


def test_resolve_hash_key_requires_at_least_32_bytes(monkeypatch):
    monkeypatch.setenv("JOIN_KEY", base64.b64encode(b"short").decode("ascii"))

    with pytest.raises(KeyResolutionError, match="at least 32 bytes"):
        resolve_key("env:JOIN_KEY", usage="hash")


def test_resolve_secrets_file_reads_bytes_and_strips_trailing_newline(tmp_path):
    path = tmp_path / "data_key"
    path.write_bytes(b"b" * 32 + b"\n")

    assert resolve_key(f"secrets:{path}", usage="encrypt") == b"b" * 32


def test_resolve_secrets_file_missing_raises(tmp_path):
    with pytest.raises(KeyResolutionError, match="not found"):
        resolve_key(f"secrets:{tmp_path / 'missing'}", usage="encrypt")
