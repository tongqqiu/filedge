import datetime
import time
import uuid

from filedge.config import load_config
from filedge.connectors import get_connector
from filedge.db import (
    Database,
    claim_processing,
    create_audit_tables,
    mark_committed,
    mark_failed,
    reclaim_stale_processing,
    reset_eligible_failed,
)
from filedge.file_registration import register_files
from filedge.loader import QuarantineOutcome, load_file
from filedge.progress import ProgressReporter, emit_progress


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

    try:
        create_audit_tables(db)

        retried = reset_eligible_failed(db, config.retry_cap)
        reclaimed = reclaim_stale_processing(db, config.stale_timeout_minutes)
        db.commit()

        connector.ensure_table(config)

        registration = register_files(
            watched_dir,
            config,
            db,
            progress=progress,
            run_id=run_id,
        )

        committed = 0
        failed = registration.failed_pre_load
        skipped = registration.skipped
        rows_committed = 0
        quarantined_rows = 0

        emit_progress(progress, "loading", "start", total=len(registration.load_candidates))
        for candidate in registration.load_candidates:
            path = candidate.path
            content_hash = candidate.content_hash
            claimed = claim_processing(db, content_hash, run_id=run_id)
            db.commit()
            if not claimed:
                emit_progress(progress, "loading", "advance", path=path)
                continue

            emit_progress(
                progress, "loading", "file_start",
                path=path, file_hash=content_hash, bytes=candidate.size,
            )
            quarantine_outcome = QuarantineOutcome()
            rows, error = load_file(
                connector,
                config,
                path,
                content_hash,
                candidate.fs,
                progress=progress,
                quarantine_outcome=quarantine_outcome,
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
                mark_committed(
                    db, content_hash, row_count=rows,
                    quarantined_row_count=quarantine_outcome.count,
                    quarantine_path=quarantine_outcome.path,
                )
                db.commit()
                committed += 1
                rows_committed += rows or 0
                quarantined_rows += quarantine_outcome.count
            else:
                mark_failed(db, content_hash, error)
                db.commit()
                failed += 1
            emit_progress(progress, "loading", "advance", path=path)
        emit_progress(progress, "loading", "finish", total=len(registration.load_candidates))

        finished_at = datetime.datetime.now(datetime.UTC).isoformat()
        duration_s = time.perf_counter() - started_perf
        return {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
            "files_scanned": registration.files_scanned,
            "new_files": registration.new_files,
            "committed": committed,
            "failed": failed,
            "skipped": skipped,
            "reclaimed": reclaimed,
            "retried": retried,
            "rows_committed": rows_committed,
            "quarantined_rows": quarantined_rows,
            "bytes_processed": registration.bytes_processed,
        }
    finally:
        connector.close()
        db.close()
