"""Typed errors for the Reference Fetcher.

All Fetcher failures derive from ``FetchError`` so the CLI can render one clean
message and exit non-zero without leaking tracebacks. Each subclass marks a
distinct failure surface (bad config, API trouble, a held Fetch Lock).
"""


class FetchError(Exception):
    """Base class for every Reference Fetcher failure."""


class SourcesConfigError(FetchError):
    """Raised when sources.yaml is missing, malformed, or names no such source."""


class SourceClientError(FetchError):
    """Raised when the API Source cannot be fetched (HTTP, rate limit, decode)."""
