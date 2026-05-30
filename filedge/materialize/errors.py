"""Typed errors for the Reference Queue Materializer.

All Materializer failures derive from ``MaterializeError`` so the CLI can render
one clean message and exit non-zero without leaking tracebacks.
"""


class MaterializeError(Exception):
    """Base class for every Reference Queue Materializer failure."""


class DecodeError(MaterializeError):
    """Raised when a queue message payload cannot be decoded into a row.

    Decoding a Micro-batch is all-or-nothing: a single undecodable payload
    fails the batch rather than silently producing bad rows (Strict Mode at the
    materialization boundary).
    """
