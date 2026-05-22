from etl.hashing import compute_hash


def test_hash_is_stable(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello world")
    assert compute_hash(str(f)) == compute_hash(str(f))


def test_hash_is_64_hex_chars(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    h = compute_hash(str(f))
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_different_content_gives_different_hash(tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_text("hello")
    f2 = tmp_path / "b.txt"
    f2.write_text("world")
    assert compute_hash(str(f1)) != compute_hash(str(f2))


def test_same_content_different_name_gives_same_hash(tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_text("same content")
    f2 = tmp_path / "b.txt"
    f2.write_text("same content")
    assert compute_hash(str(f1)) == compute_hash(str(f2))
