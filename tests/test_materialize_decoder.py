"""The Decoder turns one queue payload into one row, or fails the Micro-batch.
Feed input, assert output or DecodeError (prior art: tests/test_parser.py).
"""

import json

import pytest

from filedge.materialize.decoder import JsonDecoder, get_decoder
from filedge.materialize.errors import DecodeError


def test_decodes_a_json_object_to_a_dict():
    payload = json.dumps({"id": 1, "name": "alice"}).encode("utf-8")
    assert JsonDecoder().decode(payload) == {"id": 1, "name": "alice"}


def test_extra_fields_pass_through_unchanged():
    payload = b'{"id": 1, "unexpected": "kept"}'
    assert JsonDecoder().decode(payload) == {"id": 1, "unexpected": "kept"}


def test_malformed_json_raises_decode_error():
    with pytest.raises(DecodeError, match="not valid JSON"):
        JsonDecoder().decode(b'{"id": 1,')


@pytest.mark.parametrize("payload", [b"[1, 2, 3]", b'"a string"', b"42", b"true", b"null"])
def test_non_object_root_raises_decode_error(payload):
    with pytest.raises(DecodeError, match="must be a JSON object"):
        JsonDecoder().decode(payload)


def test_non_utf8_bytes_raise_decode_error():
    with pytest.raises(DecodeError, match="not valid UTF-8"):
        JsonDecoder().decode(b"\xff\xfe\x00bad")


def test_get_decoder_returns_json_decoder():
    assert isinstance(get_decoder("json"), JsonDecoder)


def test_get_decoder_rejects_unknown_format():
    with pytest.raises(DecodeError, match="Unsupported queue decode format"):
        get_decoder("avro")
