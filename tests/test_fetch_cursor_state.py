"""The cursor is the incremental high-water mark, advanced only after a
successful promotion. Resume-from-last, no-op-on-empty, and retry-on-failure all
hinge on its read/advance behavior.
"""

from filedge.fetch.cursor_state import CursorStore


def test_first_read_is_none(tmp_path):
    store = CursorStore(str(tmp_path / "state"))
    assert store.read("github-commits") is None


def test_advance_then_read_returns_the_cursor(tmp_path):
    store = CursorStore(str(tmp_path / "state"))
    store.advance("github-commits", "2026-05-29")
    assert store.read("github-commits") == "2026-05-29"


def test_advance_overwrites_previous_cursor(tmp_path):
    store = CursorStore(str(tmp_path / "state"))
    store.advance("github-commits", "2026-05-28")
    store.advance("github-commits", "2026-05-29")
    assert store.read("github-commits") == "2026-05-29"


def test_cursors_are_isolated_per_source(tmp_path):
    store = CursorStore(str(tmp_path / "state"))
    store.advance("commits", "c1")
    store.advance("issues", "i1")
    assert store.read("commits") == "c1"
    assert store.read("issues") == "i1"


def test_cursor_survives_a_new_store_instance(tmp_path):
    state_dir = str(tmp_path / "state")
    CursorStore(state_dir).advance("commits", "c9")
    assert CursorStore(state_dir).read("commits") == "c9"
