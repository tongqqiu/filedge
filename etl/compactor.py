import datetime
import gzip
import json
from typing import List

from etl.filesystem import get_filesystem, list_files


def compact(
    source: str,
    output: str,
    max_files: int = 1000,
    compress: bool = False,
) -> dict:
    src_fs, src_root = get_filesystem(source)
    out_fs, out_root = get_filesystem(output)

    files = list_files(src_fs, src_root)
    if not files:
        return {"batches": 0, "files_compacted": 0}

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
    extension = ".ndjson.gz" if compress else ".ndjson"

    batches_written = 0
    for batch_index, batch_start in enumerate(range(0, len(files), max_files)):
        batch = files[batch_start : batch_start + max_files]
        out_name = f"{timestamp}_{batch_index + 1:04d}{extension}"
        out_path = f"{out_root}/{out_name}"

        _write_batch(src_fs, batch, out_fs, out_path, compress)
        batches_written += 1

    return {"batches": batches_written, "files_compacted": len(files)}


def _write_batch(src_fs, files: List[str], out_fs, out_path: str, compress: bool) -> None:
    open_out = out_fs.open if out_fs is not None else open

    with open_out(out_path, "wb") as raw:
        dest = gzip.GzipFile(fileobj=raw, mode="wb") if compress else raw
        try:
            for path in files:
                _copy_rows(src_fs, path, dest)
        finally:
            if compress:
                dest.close()


def _copy_rows(src_fs, path: str, dest) -> None:
    open_src = src_fs.open if src_fs is not None else open
    with open_src(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # validate JSON then re-emit to normalise whitespace
            obj = json.loads(line)
            dest.write((json.dumps(obj) + "\n").encode())
