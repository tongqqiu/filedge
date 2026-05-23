import hashlib


def compute_hash(path: str, fs=None) -> str:
    h = hashlib.sha256()
    ctx = fs.open(path, "rb") if fs is not None else open(path, "rb")
    with ctx as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
