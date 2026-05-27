import base64
import hashlib
import hmac
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from filedge.config import ColumnMapping, EncryptConfig, HashConfig
from filedge.field_crypto import FieldCryptoEngine


ENCRYPTION_KEY = b"e" * 32
HASH_KEY = b"h" * 32


def _keys(reference: str, usage: str) -> bytes:
    assert usage in ("encrypt", "hash")
    return {
        "env:DATA_KEY": ENCRYPTION_KEY,
        "env:JOIN_KEY": HASH_KEY,
    }[reference]


def test_encrypt_column_produces_framed_aes_gcm_ciphertext():
    engine = FieldCryptoEngine.for_pipeline(
        [
            ColumnMapping(
                "ssn",
                "ssn_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            )
        ],
        _keys,
    )

    out = engine.apply_to_row({"ssn_ct": "123-45-6789"})
    framed = base64.b64decode(out["ssn_ct"])

    assert framed[0] == 1
    assert len(framed) == 1 + 12 + 16 + len("123-45-6789")
    nonce = framed[1:13]
    tag = framed[13:29]
    ciphertext = framed[29:]
    assert AESGCM(ENCRYPTION_KEY).decrypt(nonce, ciphertext + tag, None) == b"123-45-6789"


def test_encrypting_same_plaintext_twice_produces_different_ciphertext():
    engine = FieldCryptoEngine.for_pipeline(
        [
            ColumnMapping(
                "ssn",
                "ssn_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            )
        ],
        _keys,
    )

    first = engine.apply_to_row({"ssn_ct": "123-45-6789"})
    second = engine.apply_to_row({"ssn_ct": "123-45-6789"})

    assert first["ssn_ct"] != second["ssn_ct"]


def test_hash_column_produces_stable_hmac_sha256_token():
    engine = FieldCryptoEngine.for_pipeline(
        [
            ColumnMapping(
                "email",
                "email_join",
                "string",
                hash=HashConfig("hmac-sha256", "env:JOIN_KEY"),
            )
        ],
        _keys,
    )

    first = engine.apply_to_row({"email_join": "person@example.com"})
    second = engine.apply_to_row({"email_join": "person@example.com"})

    expected = base64.b64encode(
        hmac.new(HASH_KEY, b"person@example.com", hashlib.sha256).digest()
    ).decode("ascii")
    assert first["email_join"] == expected
    assert second["email_join"] == expected


def test_same_source_value_can_produce_ciphertext_and_token_columns():
    engine = FieldCryptoEngine.for_pipeline(
        [
            ColumnMapping(
                "ssn",
                "ssn_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            ),
            ColumnMapping(
                "ssn",
                "ssn_join",
                "string",
                hash=HashConfig("hmac-sha256", "env:JOIN_KEY"),
            ),
        ],
        _keys,
    )

    out = engine.apply_to_row({"ssn_ct": "123-45-6789", "ssn_join": "123-45-6789"})

    assert out["ssn_ct"] != "123-45-6789"
    assert out["ssn_join"] != "123-45-6789"
    assert set(out) == {"ssn_ct", "ssn_join"}


def test_encryption_performance_smoke_budget():
    engine = FieldCryptoEngine.for_pipeline(
        [
            ColumnMapping(
                "pii_1",
                "pii_1_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            ),
            ColumnMapping(
                "pii_2",
                "pii_2_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            ),
            ColumnMapping(
                "pii_3",
                "pii_3_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            ),
        ],
        _keys,
    )
    row = {
        "pii_1_ct": "a" * 64,
        "pii_2_ct": "b" * 64,
        "pii_3_ct": "c" * 64,
    }

    start = time.perf_counter()
    for _ in range(50_000):
        engine.apply_to_row(row)
    elapsed = time.perf_counter() - start

    assert elapsed < 15
