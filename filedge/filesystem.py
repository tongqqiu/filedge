import os
from typing import List, Tuple

_PROTOCOL_TO_EXTRA = {
    "gs": "gcs",
    "gcs": "gcs",
    "s3": "s3",
    "s3a": "s3",
}


def get_filesystem(path: str) -> Tuple:
    """Return (fs, root_path). fs is None for local paths."""
    if "://" in path and not path.startswith("file://"):
        protocol = path.split("://")[0]
        extra = _PROTOCOL_TO_EXTRA.get(protocol, "")
        try:
            import fsspec
        except ImportError as e:
            hint = f" — run: pip install filedge[{extra}]" if extra else ""
            raise ImportError(f"Cloud paths require fsspec{hint}") from e
        fs, root = fsspec.url_to_fs(path)
        return fs, root
    return None, path


def open_file(path: str, fs=None, mode: str = "r", encoding: str = "utf-8"):
    """Open a local or remote file, returning a file-like object."""
    if "b" in mode:
        return fs.open(path, mode) if fs is not None else open(path, mode)
    if fs is not None:
        return fs.open(path, mode, encoding=encoding)
    return open(path, mode, encoding=encoding, newline="")


def list_files(fs, path: str, file_pattern: str | None = None) -> List[str]:
    """List files directly under path, sorted. Optionally filter by glob pattern.

    Files ending in .tmp are always excluded — they are in-progress writes from
    compact and must never be picked up by a concurrent or immediately-following run.
    Files ending in .manifest.json are also excluded — they are source manifest
    sidecars (ADR-0011), discovered via the data File they sit next to.
    """
    def _accept(p: str) -> bool:
        return not p.endswith(".tmp") and not p.endswith(".manifest.json")

    if file_pattern is not None:
        pattern = path.rstrip("/") + "/" + file_pattern
        if fs is None:
            import glob as glob_mod
            return sorted(
                p for p in glob_mod.glob(pattern)
                if os.path.isfile(p) and _accept(p)
            )
        return sorted(p for p in fs.glob(pattern) if _accept(p))
    if fs is None:
        return sorted(
            os.path.join(path, name)
            for name in os.listdir(path)
            if os.path.isfile(os.path.join(path, name)) and _accept(name)
        )
    entries = fs.ls(path, detail=True)
    return sorted(
        e["name"] for e in entries
        if e["type"] == "file" and _accept(e["name"])
    )


def file_basename(path: str) -> str:
    """Filename component of a local or cloud path."""
    return path.split("/")[-1]


def file_size(path: str, fs=None) -> int:
    """Size in bytes of a local or remote file."""
    if fs is not None:
        return fs.size(path)
    return os.path.getsize(path)
