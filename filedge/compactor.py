import datetime
import gzip
import json
import os
from typing import List

from filedge.filesystem import file_basename, get_filesystem, list_files

# Manifest lives in a hidden subdirectory so filedge run never picks it up.
_MANIFEST_SUBDIR = ".filedge"
_MANIFEST_FILE = "compact_manifest.ndjson"


def compact(
    source: str,
    output: str,
    max_files: int = 1000,
    compress: bool = False,
    delete_source: bool = False,
) -> dict:
    """Merge small NDJSON files from source into batched output files.

    Resilience model
    ----------------
    Each batch is written atomically via a .tmp → rename sequence. On crash,
    the .tmp file is cleaned up and the batch is retried on the next run.

    delete_source=True (has delete permission):
        Source files are deleted after each batch commits. The absence of source
        files is the idempotency signal — re-running is safe because there is
        nothing left to process.

    delete_source=False (read-only source, default):
        A manifest is maintained at <output>/.filedge/compact_manifest.ndjson.
        Each line records the source basenames included in one batch. On startup
        the manifest is read and already-processed files are skipped. Duplicate
        batches in the output directory are harmless because filedge run
        deduplicates by content hash.
    """
    src_fs, src_root = get_filesystem(source)
    out_fs, out_root = get_filesystem(output)

    files = list_files(src_fs, src_root)
    if not files:
        return {"batches": 0, "files_compacted": 0}

    if not delete_source:
        already_done = _load_manifest(out_fs, out_root)
        files = [f for f in files if file_basename(f) not in already_done]
        if not files:
            return {"batches": 0, "files_compacted": 0}

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%f")
    extension = ".ndjson.gz" if compress else ".ndjson"

    batches_written = 0
    files_compacted = 0
    for batch_index, batch_start in enumerate(range(0, len(files), max_files)):
        batch = files[batch_start : batch_start + max_files]
        out_name = f"{timestamp}_{batch_index + 1:04d}{extension}"
        out_path = f"{out_root}/{out_name}"

        _write_batch(src_fs, batch, out_fs, out_path, compress)
        batches_written += 1
        files_compacted += len(batch)

        if delete_source:
            for path in batch:
                _delete_file(src_fs, path)
        else:
            _append_manifest(out_fs, out_root, out_name, [file_basename(f) for f in batch])

    return {"batches": batches_written, "files_compacted": files_compacted}


def _manifest_path(out_root: str) -> str:
    return f"{out_root}/{_MANIFEST_SUBDIR}/{_MANIFEST_FILE}"


def _load_manifest(out_fs, out_root: str) -> set:
    """Return the set of source basenames already recorded in the manifest."""
    path = _manifest_path(out_root)
    open_fn = out_fs.open if out_fs is not None else open
    try:
        with open_fn(path, "r", encoding="utf-8") as f:
            seen = set()
            for line in f:
                line = line.strip()
                if line:
                    seen.update(json.loads(line).get("sources", []))
            return seen
    except (FileNotFoundError, OSError):
        return set()


def _append_manifest(out_fs, out_root: str, batch_name: str, sources: List[str]) -> None:
    """Append one manifest entry recording which source files went into batch_name."""
    entry = json.dumps({"batch": batch_name, "sources": sources}) + "\n"
    path = _manifest_path(out_root)

    if out_fs is None:
        os.makedirs(os.path.join(out_root, _MANIFEST_SUBDIR), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        # Cloud storage (S3/GCS) does not support append — read + rewrite.
        existing = ""
        try:
            with out_fs.open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except (FileNotFoundError, OSError):
            pass
        with out_fs.open(path, "w", encoding="utf-8") as f:
            f.write(existing + entry)


def _write_batch(src_fs, files: List[str], out_fs, out_path: str, compress: bool) -> None:
    open_out = out_fs.open if out_fs is not None else open
    rename = out_fs.rename if out_fs is not None else os.replace
    unlink = out_fs.rm if out_fs is not None else os.unlink
    tmp_path = out_path + ".tmp"
    success = False

    try:
        with open_out(tmp_path, "wb") as raw:
            if compress:
                with gzip.GzipFile(fileobj=raw, mode="wb") as dest:
                    for path in files:
                        _copy_rows(src_fs, path, dest)
            else:
                for path in files:
                    _copy_rows(src_fs, path, raw)
        rename(tmp_path, out_path)
        success = True
    finally:
        if not success:
            try:
                unlink(tmp_path)
            except OSError:
                pass


def _copy_rows(src_fs, path: str, dest) -> None:
    open_src = src_fs.open if src_fs is not None else open
    with open_src(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            dest.write((json.dumps(obj) + "\n").encode())


def _delete_file(fs, path: str) -> None:
    if fs is not None:
        fs.rm(path)
    else:
        os.unlink(path)
