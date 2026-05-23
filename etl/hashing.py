import hashlib

from etl.filesystem import open_file


def compute_hash(path: str, fs=None) -> str:
    h = hashlib.sha256()
    with open_file(path, fs=fs, mode="rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
