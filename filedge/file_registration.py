import os
from dataclasses import dataclass

from filedge.config import PipelineConfig
from filedge.db import (
    Database,
    claim_processing,
    get_hash_states,
    insert_pending,
    mark_failed,
)
from filedge.filesystem import file_basename, file_size, get_filesystem, list_files
from filedge.hashing import compute_hash
from filedge.progress import ProgressReporter, emit_progress
from filedge.source_manifest import discover_and_parse


@dataclass(frozen=True)
class LoadCandidate:
    path: str
    content_hash: str
    size: int
    fs: object = None


@dataclass(frozen=True)
class PreLoadFailure:
    path: str
    content_hash: str
    error: str


@dataclass(frozen=True)
class FileRegistrationResult:
    load_candidates: list[LoadCandidate]
    pre_load_failures: list[PreLoadFailure]
    files_scanned: int
    new_files: int
    failed_pre_load: int
    skipped: int
    bytes_processed: int


def _watched_dir_missing(fs, root: str) -> bool:
    """True when the Watched Directory does not exist yet (local or remote)."""
    if fs is None:
        return not os.path.isdir(root)
    return not fs.isdir(root)


def register_files(
    watched_dir: str,
    config: PipelineConfig,
    db: Database,
    progress: ProgressReporter | None = None,
    run_id: str | None = None,
) -> FileRegistrationResult:
    fs, root = get_filesystem(watched_dir)
    # A not-yet-created Watched Directory is treated as empty rather than an
    # error, so a scheduled run that fires before the first File is dropped
    # (e.g. before the first fetch on a fresh volume) is a clean no-op.
    if _watched_dir_missing(fs, root):
        files = []
    else:
        files = list_files(fs, root, file_pattern=config.file_pattern)

    emit_progress(progress, "hashing", "start", total=len(files))
    file_hashes = {}
    file_sizes = {}
    bytes_processed = 0
    for path in files:
        file_hashes[path] = compute_hash(path, fs)
        file_sizes[path] = file_size(path, fs)
        bytes_processed += file_sizes[path]
        emit_progress(progress, "hashing", "advance", path=path)
    emit_progress(progress, "hashing", "finish", total=len(files))

    emit_progress(progress, "registering", "start", total=len(files))
    hash_states = get_hash_states(db, list(file_hashes.values()))
    new_files = 0
    manifest_errors: dict[str, str] = {}
    for path in files:
        content_hash = file_hashes[path]
        if content_hash not in hash_states:
            metadata = None
            if config.source_manifest != "disabled":
                manifest = discover_and_parse(path, fs=fs)
                if manifest.metadata is not None:
                    metadata = manifest.metadata
                elif config.source_manifest == "required":
                    manifest_errors[content_hash] = (
                        f"{manifest.error_category}: {manifest.manifest_path}"
                    )
            insert_pending(
                db,
                file_basename(path),
                content_hash,
                source_dir=watched_dir,
                source_metadata=metadata,
            )
            hash_states[content_hash] = "PENDING"
            new_files += 1
        emit_progress(progress, "registering", "advance", path=path)
    db.commit()

    load_candidates: list[LoadCandidate] = []
    pre_load_failures: list[PreLoadFailure] = []
    failed_pre_load = skipped = 0
    for path in files:
        content_hash = file_hashes[path]
        if content_hash in manifest_errors:
            claim_processing(db, content_hash, run_id=run_id)
            mark_failed(db, content_hash, manifest_errors[content_hash])
            db.commit()
            pre_load_failures.append(
                PreLoadFailure(
                    path=path,
                    content_hash=content_hash,
                    error=manifest_errors[content_hash],
                )
            )
            failed_pre_load += 1
            continue
        state = hash_states.get(content_hash)
        if state != "PENDING":
            if state == "FAILED":
                skipped += 1
            continue
        load_candidates.append(
            LoadCandidate(
                path=path,
                content_hash=content_hash,
                size=file_sizes[path],
                fs=fs,
            )
        )

    emit_progress(progress, "registering", "finish", total=len(files))
    return FileRegistrationResult(
        load_candidates=load_candidates,
        pre_load_failures=pre_load_failures,
        files_scanned=len(files),
        new_files=new_files,
        failed_pre_load=failed_pre_load,
        skipped=skipped,
        bytes_processed=bytes_processed,
    )
