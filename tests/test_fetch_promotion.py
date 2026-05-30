"""Promotion is the reliability core: a partial fetch must never become a visible
File, the File must never appear without its sidecar, and two concurrent fetches
for one source must not race.
"""

import os

import pytest

from filedge.fetch.errors import FetchLockHeld
from filedge.fetch.promotion import FetchLock, promote


def _stage(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    data = staging / "src-2026-05-01.ndjson"
    data.write_text('{"id": 1}\n')
    sidecar = staging / "src-2026-05-01.ndjson.manifest.json"
    sidecar.write_text('{"job": {}}')
    return str(data), str(sidecar)


def test_promote_lands_both_data_and_sidecar_in_watched_directory(tmp_path):
    data, sidecar = _stage(tmp_path)
    watched = tmp_path / "landing"

    result = promote(data, sidecar, str(watched))

    assert os.path.isfile(result.data_path)
    assert os.path.isfile(result.sidecar_path)
    # The sidecar sits next to the data File under the reader's expected suffix.
    assert result.sidecar_path == result.data_path + ".manifest.json"
    # Staging no longer holds them — they were moved, not copied.
    assert not os.path.exists(data)
    assert not os.path.exists(sidecar)


def test_nothing_is_visible_in_watched_directory_before_promotion(tmp_path):
    _stage(tmp_path)
    watched = tmp_path / "landing"
    watched.mkdir()

    assert os.listdir(watched) == []


def test_data_file_is_never_promoted_without_its_sidecar(tmp_path, monkeypatch):
    # Simulate a crash after the sidecar moves but before the data File moves:
    # the Watched Directory must not contain a data File the reader would treat
    # as a trigger without provenance.
    data, sidecar = _stage(tmp_path)
    watched = tmp_path / "landing"

    import filedge.fetch.promotion as promotion_mod

    real_move = promotion_mod._move
    calls = {"n": 0}

    def flaky_move(src, dest):
        calls["n"] += 1
        if calls["n"] == 2:  # the data File move (sidecar moved first)
            raise OSError("disk gone")
        return real_move(src, dest)

    monkeypatch.setattr(promotion_mod, "_move", flaky_move)

    with pytest.raises(OSError):
        promote(data, sidecar, str(watched))

    landed = os.listdir(watched)
    assert all(not name.endswith(".ndjson") for name in landed)


def test_fetch_lock_blocks_a_second_concurrent_holder(tmp_path):
    lock_dir = tmp_path / "state"

    with FetchLock(str(lock_dir), "src"):
        with pytest.raises(FetchLockHeld):
            with FetchLock(str(lock_dir), "src"):
                pass


def test_fetch_lock_is_released_on_exit_and_reacquirable(tmp_path):
    lock_dir = tmp_path / "state"

    with FetchLock(str(lock_dir), "src"):
        pass
    # No FetchLockHeld — the lock was released.
    with FetchLock(str(lock_dir), "src"):
        pass


def test_fetch_lock_is_released_even_when_body_raises(tmp_path):
    lock_dir = tmp_path / "state"

    with pytest.raises(ValueError):
        with FetchLock(str(lock_dir), "src"):
            raise ValueError("boom")

    with FetchLock(str(lock_dir), "src"):  # reacquirable
        pass


def test_fetch_lock_exit_tolerates_an_already_removed_lock(tmp_path):
    lock_dir = tmp_path / "state"
    lock = FetchLock(str(lock_dir), "src")
    with lock:
        os.rmdir(lock._path)  # something else cleaned it up first
    # __exit__ must not raise on the missing lock dir.


def test_promote_falls_back_to_copy_when_rename_crosses_filesystems(tmp_path, monkeypatch):
    data, sidecar = _stage(tmp_path)
    watched = tmp_path / "landing"

    import filedge.fetch.promotion as promotion_mod

    def cross_device(src, dest):
        raise OSError("EXDEV: cross-device link")

    monkeypatch.setattr(promotion_mod.os, "replace", cross_device)

    result = promote(data, sidecar, str(watched))

    assert os.path.isfile(result.data_path)
    assert os.path.isfile(result.sidecar_path)
    assert not os.path.exists(data)  # copied then removed from staging
    assert not os.path.exists(sidecar)
