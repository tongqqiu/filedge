import base64
import binascii
import os
from pathlib import Path


class KeyResolutionError(Exception):
    pass


def resolve_key(reference: str, usage: str) -> bytes:
    if reference.startswith("env:"):
        key = _resolve_env_key(reference.removeprefix("env:"))
    elif reference.startswith("secrets:/"):
        key = _resolve_secret_key(reference.removeprefix("secrets:"))
    else:
        raise KeyResolutionError(
            "Field Encryption key reference must use env:NAME or secrets:/absolute/path"
        )
    _validate_key_length(key, usage)
    return key


def _resolve_env_key(name: str) -> bytes:
    if not name:
        raise KeyResolutionError("env key reference requires a variable name")
    value = os.environ.get(name)
    if value is None:
        raise KeyResolutionError(f"Environment variable {name!r} is not set")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise KeyResolutionError(
            f"Environment variable {name!r} must contain base64 key material"
        ) from e


def _resolve_secret_key(path: str) -> bytes:
    if not path.startswith("/"):
        raise KeyResolutionError("secrets key reference requires an absolute path")
    try:
        return Path(path).read_bytes().removesuffix(b"\n")
    except FileNotFoundError as e:
        raise KeyResolutionError(f"Secrets file {path!r} not found") from e
    except OSError as e:
        raise KeyResolutionError(f"Cannot read secrets file {path!r}: {e}") from e


def _validate_key_length(key: bytes, usage: str) -> None:
    if usage == "encrypt":
        if len(key) != 32:
            raise KeyResolutionError("AES-256-GCM keys must be exactly 32 bytes")
        return
    if usage == "hash":
        if len(key) < 32:
            raise KeyResolutionError("HMAC-SHA256 keys must be at least 32 bytes")
        return
    raise KeyResolutionError(f"Unknown key usage: {usage!r}")
