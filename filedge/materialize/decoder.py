"""Decode queue message payloads into rows.

A Queue Materializer reads opaque ``bytes`` off the broker; before they can be
written as NDJSON they must become JSON-object rows. The Decoder is the seam
that turns one payload into one ``dict``. The default ``JsonDecoder`` parses a
UTF-8 JSON object; anything else — malformed JSON, a non-object root, or
non-UTF-8 bytes — raises ``DecodeError`` and fails the Micro-batch.

Extra fields pass through unchanged: Column Tolerance and validation are
Transform's job at `filedge run`, not the Decoder's. Avro/Protobuf and Schema
Registry are out of scope; the interface leaves room for them later.
"""

import json
from typing import Protocol, runtime_checkable

from filedge.materialize.errors import DecodeError


@runtime_checkable
class Decoder(Protocol):
    """Turn one queue message payload into one row."""

    def decode(self, payload: bytes) -> dict:  # pragma: no cover - interface
        ...


class JsonDecoder:
    """Decode a UTF-8 JSON-object payload into a row dict."""

    def decode(self, payload: bytes) -> dict:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as e:
            raise DecodeError("Queue payload is not valid UTF-8.") from e
        try:
            value = json.loads(text)
        except json.JSONDecodeError as e:
            raise DecodeError(f"Queue payload is not valid JSON: {e}.") from e
        if not isinstance(value, dict):
            raise DecodeError(
                f"Queue payload must be a JSON object, got {type(value).__name__}."
            )
        return value


_DECODERS = {"json": JsonDecoder}


def get_decoder(fmt: str) -> Decoder:
    """Return the Decoder for a `format:` value, or raise for an unknown one."""
    try:
        return _DECODERS[fmt]()
    except KeyError:
        known = ", ".join(repr(k) for k in _DECODERS)
        raise DecodeError(f"Unsupported queue decode format {fmt!r}; known: {known}.")
