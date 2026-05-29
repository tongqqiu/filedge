"""The shared `env:NAME` / `secrets:/absolute/path` reference seam.

Filedge never writes a secret into an authored artifact: an Audit DB connection
or a Field Encryption key is declared as a *reference* — `env:NAME` for an
environment variable or `secrets:/abs/path` for a secrets-mount file — and
resolved to its backing value only at command time. This module owns the prefix
dispatch and the read-and-validate of those two reference forms, so the
Field Encryption key resolver and the Pipeline Registry's `audit_db` resolution
sit on one seam instead of each re-implementing the parsing.

`resolve_reference` is the string front door (e.g. an `audit_db` connection
string). Consumers that need raw bytes — key material may be binary — use the
`split_reference` / `read_env` / `read_secret` leaves directly so they can decode
on their own terms. Every failure is a `ReferenceError` naming the offending
variable or path.
"""

import os
from pathlib import Path
from typing import Tuple

_ENV_PREFIX = "env:"
_SECRETS_PREFIX = "secrets:"


class ReferenceError(Exception):
    """Raised when an env:/secrets: reference is malformed or cannot be read."""


def split_reference(reference: str, *, usage: str) -> Tuple[str, str]:
    """Classify a reference as ``("env", name)`` or ``("secrets", path)``.

    Raises ``ReferenceError`` for any other shape — a literal value, a bare
    name, or an unknown scheme. ``usage`` names the caller's setting (e.g.
    ``"audit_db"``) so the message points at the right declaration.
    """
    if reference.startswith(_ENV_PREFIX):
        return "env", reference[len(_ENV_PREFIX):]
    if reference.startswith(_SECRETS_PREFIX + "/"):
        return "secrets", reference[len(_SECRETS_PREFIX):]
    raise ReferenceError(
        f"{usage} reference must use env:NAME or secrets:/absolute/path"
    )


def read_env(name: str, *, usage: str) -> str:
    """Read an environment variable's value, raising if unnamed or unset."""
    if not name:
        raise ReferenceError(f"{usage} env reference requires a variable name")
    value = os.environ.get(name)
    if value is None:
        raise ReferenceError(f"Environment variable {name!r} is not set")
    return value


def read_secret(path: str, *, usage: str) -> bytes:
    """Read a secrets-mount file's raw bytes, raising if absent or unreadable."""
    if not path.startswith("/"):
        raise ReferenceError(f"{usage} secrets reference requires an absolute path")
    try:
        return Path(path).read_bytes()
    except FileNotFoundError as e:
        raise ReferenceError(f"Secrets file {path!r} not found") from e
    except OSError as e:
        raise ReferenceError(f"Cannot read secrets file {path!r}: {e}") from e


def resolve_reference(reference: str, *, usage: str) -> str:
    """Resolve an env:/secrets: reference to a string value.

    An ``env:`` reference yields the variable's value verbatim; a ``secrets:``
    reference yields the file's UTF-8 contents with a single trailing newline
    stripped (matching how a connection string is conventionally stored).
    """
    scheme, rest = split_reference(reference, usage=usage)
    if scheme == "env":
        return read_env(rest, usage=usage)
    return read_secret(rest, usage=usage).decode("utf-8").rstrip("\n")
