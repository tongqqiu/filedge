import datetime
import time
import uuid

from filedge.config import load_config
from filedge.connectors import get_connector
from filedge.db import (
    Database,
    claim_processing,
    create_audit_tables,
    get_hash_states,
    insert_pending,
    mark_committed,
    mark_failed,
    reclaim_stale_processing,
    reset_eligible_failed,
)
from filedge.filesystem import file_basename, file_size, get_filesystem, list_files
from filedge.hashing import compute_hash
from filedge.loader import load_file
from filedge.progress import ProgressReporter, emit_progress
from filedge.source_manifest import discover_and_parse


def run_pipeline(
    watched_dir: str,
    config_path: str,
    audit_db_url: str,
    progress: ProgressReporter | None = None,
    run_id: str | None = None,
    preflight: bool = True,
) -> dict:
    if run_id is None:
        run_id = str(uuid.uuid4())
    started_at = datetime.datetime.now(datetime.UTC).isoformat()
    started_perf = time.perf_counter()
    config = load_config(config_path)
    if preflight:
        from filedge.health import assert_healthy
        assert_healthy(config, audit_db_url)

    db = Database(audit_db_url)
    connector = get_connector(config)
    fs, root = get_filesystem(watched_dir)

    try:
        create_audit_tables(db)

        retried = reset_eligible_failed(db, config.retry_cap)
        reclaimed = reclaim_stale_processing(db, config.stale_timeout_minutes)
        db.commit()

        connector.ensure_table(config)

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
        for path in files:
            content_hash = file_hashes[path]
            if content_hash not in hash_states:
                manifest = discover_and_parse(path, fs=fs)
                insert_pending(
                    db,
                    file_basename(path),
                    content_hash,
                    source_dir=watched_dir,
                    source_metadata=manifest.metadata if manifest.found else None,
                )
                hash_states[content_hash] = "PENDING"
                new_files += 1
            emit_progress(progress, "registering", "advance", path=path)
        db.commit()
        emit_progress(progress, "registering", "finish", total=len(files))

        committed = failed = skipped = 0
        rows_committed = 0
        pending_files = []
        for path in files:
            content_hash = file_hashes[path]
            state = hash_states.get(content_hash)
            if state != "PENDING":
                if state == "FAILED":
                    skipped += 1
                continue
            pending_files.append((path, content_hash))

        emit_progress(progress, "loading", "start", total=len(pending_files))
        for path, content_hash in pending_files:
            claimed = claim_processing(db, content_hash, run_id=run_id)
            db.commit()
            if not claimed:
                emit_progress(progress, "loading", "advance", path=path)
                continue

            emit_progress(
                progress, "loading", "file_start",
                path=path, file_hash=content_hash, bytes=file_sizes.get(path),
            )
            rows, error = load_file(
                connector,
                config,
                path,
                content_hash,
                fs,
                progress=progress,
            )
            emit_progress(
                progress,
                "loading",
                "file_finish",
                path=path,
                rows=rows,
                error=error,
            )

            if error is None:
                mark_committed(db, content_hash, row_count=rows)
                db.commit()
                committed += 1
                rows_committed += rows or 0
            else:
                mark_failed(db, content_hash, error)
                db.commit()
                failed += 1
            emit_progress(progress, "loading", "advance", path=path)
        emit_progress(progress, "loading", "finish", total=len(pending_files))

        finished_at = datetime.datetime.now(datetime.UTC).isoformat()
        duration_s = time.perf_counter() - started_perf
        return {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
            "files_scanned": len(files),
            "new_files": new_files,
            "committed": committed,
            "failed": failed,
            "skipped": skipped,
            "reclaimed": reclaimed,
            "retried": retried,
            "rows_committed": rows_committed,
            "bytes_processed": bytes_processed,
        }
    finally:
        connector.close()
        db.close()
