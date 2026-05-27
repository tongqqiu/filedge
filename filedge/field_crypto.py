import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from filedge.config import ColumnMapping

KeyResolver = Callable[[str, str], bytes]

ENCRYPTION_FRAME_VERSION = 1
NONCE_BYTES = 12


class FieldCryptoError(Exception):
    pass


@dataclass(frozen=True)
class _EncryptOperation:
    dest: str
    aesgcm: AESGCM


@dataclass(frozen=True)
class _HashOperation:
    dest: str
    key: bytes


class FieldCryptoEngine:
    def __init__(
        self,
        encrypt_operations: list[_EncryptOperation],
        hash_operations: list[_HashOperation],
    ) -> None:
        self._encrypt_operations = encrypt_operations
        self._hash_operations = hash_operations

    @classmethod
    def for_pipeline(
        cls, columns: list[ColumnMapping], key_resolver: KeyResolver
    ) -> "FieldCryptoEngine":
        encrypt_operations = []
        hash_operations = []
        for column in columns:
            if column.encrypt is not None:
                key = key_resolver(column.encrypt.key, "encrypt")
                encrypt_operations.append(
                    _EncryptOperation(column.dest, AESGCM(key))
                )
            if column.hash is not None:
                key = key_resolver(column.hash.key, "hash")
                hash_operations.append(_HashOperation(column.dest, key))
        return cls(encrypt_operations, hash_operations)

    def apply_to_row(self, row: dict[str, Any]) -> dict[str, Any]:
        encrypted = dict(row)
        try:
            for operation in self._encrypt_operations:
                value = _string_bytes(encrypted[operation.dest])
                nonce = secrets.token_bytes(NONCE_BYTES)
                encrypted_with_tag = operation.aesgcm.encrypt(nonce, value, None)
                ciphertext = encrypted_with_tag[:-16]
                tag = encrypted_with_tag[-16:]
                frame = (
                    bytes([ENCRYPTION_FRAME_VERSION])
                    + nonce
                    + tag
                    + ciphertext
                )
                encrypted[operation.dest] = base64.b64encode(frame).decode("ascii")
            for operation in self._hash_operations:
                value = _string_bytes(encrypted[operation.dest])
                digest = hmac.new(operation.key, value, hashlib.sha256).digest()
                encrypted[operation.dest] = base64.b64encode(digest).decode("ascii")
        except Exception as e:
            raise FieldCryptoError(f"Field Encryption failed: {e}") from e
        return encrypted


def _string_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    return str(value).encode("utf-8")
