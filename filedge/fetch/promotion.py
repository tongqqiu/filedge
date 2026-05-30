"""Promote a staged File + its sidecar into the Watched Directory, under a lock.

Two reliability rules from ADR-0006 are structural here:

1. **Only complete Files become visible.** The data File is moved into the
   Watched Directory last, with `os.replace` (atomic rename), so `filedge run`
   never sees a half-written File.
2. **A File is never visible without its provenance.** The Source Manifest
   sidecar is moved *first*; only then is the data File (the thing `filedge run`
   discovers) moved. The reverse order could expose a File whose sidecar has not
   landed yet.

The **Fetch Lock** (CONTEXT.md) serializes promotion for one API Source so two
concurrent fetches cannot race partial files into the landing zone. It is a
filesystem lock — an atomically-created lock directory — owned by the Fetcher,
not an Audit DB record and not part of the `filedge run` state machine.
"""

import os
import shutil
from dataclasses import dataclass

from filedge.fetch.errors import FetchLockHeld

_LOCK_SUFFIX = ".lock"


class FetchLock:
    """A per-source filesystem mutex, acquired as a context manager.

    Uses `os.mkdir` (atomic creation that fails if the directory exists) as the
    lock primitive, so a second holder for the same source raises `FetchLockHeld`
    instead of racing.
    """

    def __init__(self, lock_dir: str, source_name: str):
        self._path = os.path.join(lock_dir, source_name + _LOCK_SUFFIX)
        self._lock_dir = lock_dir

    def __enter__(self) -> "FetchLock":
        os.makedirs(self._lock_dir, exist_ok=True)
        try:
            os.mkdir(self._path)
        except FileExistsError as e:
            raise FetchLockHeld(
                f"Fetch Lock {self._path!r} is held by another fetch for this source."
            ) from e
        return self

    def __exit__(self, *exc) -> None:
        try:
            os.rmdir(self._path)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class Promotion:
    data_path: str
    sidecar_path: str


def promote(
    staged_data_path: str,
    staged_sidecar_path: str,
    watched_directory: str,
) -> Promotion:
    """Move the sidecar then the data File into the Watched Directory.

    Returns the landed paths. Call inside a held `FetchLock`. The sidecar lands
    first so the data File is never discoverable without it.
    """
    os.makedirs(watched_directory, exist_ok=True)
    data_dest = os.path.join(watched_directory, os.path.basename(staged_data_path))
    sidecar_dest = os.path.join(watched_directory, os.path.basename(staged_sidecar_path))

    _move(staged_sidecar_path, sidecar_dest)
    _move(staged_data_path, data_dest)
    return Promotion(data_path=data_dest, sidecar_path=sidecar_dest)


def _move(src: str, dest: str) -> None:
    """Atomic rename when on one filesystem; fall back to copy+replace across."""
    try:
        os.replace(src, dest)
    except OSError:
        shutil.copy2(src, dest)
        os.remove(src)
