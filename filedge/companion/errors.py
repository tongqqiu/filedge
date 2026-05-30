"""Typed errors for the shared companion building blocks."""


class CompanionError(Exception):
    """Base class for shared Fetcher/Materializer companion failures."""


class FetchLockHeld(CompanionError):
    """Raised when another process holds the Fetch Lock for this source.

    The Fetch Lock serializes promotion per source (CONTEXT.md: Fetch Lock);
    both the Reference Fetcher and the Reference Queue Materializer use it.
    """
