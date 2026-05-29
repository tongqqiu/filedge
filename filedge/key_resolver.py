import base64
import binascii

from filedge.reference import (
    ReferenceError,
    read_env,
    read_secret,
    split_reference,
)

_USAGE = "Field Encryption key"


class KeyResolutionError(Exception):
    pass


def resolve_key(reference: str, usage: str) -> bytes:
    try:
        scheme, rest = split_reference(reference, usage=_USAGE)
        if scheme == "env":
            key = _decode_env_key(rest)
        else:
            key = read_secret(rest, usage=_USAGE).removesuffix(b"\n")
    except ReferenceError as e:
        raise KeyResolutionError(str(e)) from e
    _validate_key_length(key, usage)
    return key


def _decode_env_key(name: str) -> bytes:
    value = read_env(name, usage=_USAGE)
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise KeyResolutionError(
            f"Environment variable {name!r} must contain base64 key material"
        ) from e


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
