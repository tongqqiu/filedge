import os

from etl.config import load_config
from etl.db import (
    Database,
    SchemaError,
    claim_processing,
    create_audit_tables,
    ensure_destination_table,
    find_file_by_hash,
    insert_pending,
    mark_committed,
    mark_failed,
    reclaim_stale_processing,
)
from etl.hashing import compute_hash
from etl.loader import load_file


def run_pipeline(watched_dir: str, config_path: str, db_url: str) -> dict:
    """
    Execute one Run: scan Watched Directory, enqueue new Files as PENDING,
    load each PENDING File, commit records + COMMITTED marker in a single transaction.
    """
    config = load_config(config_path)
    db = Database(db_url)

    try:
        create_audit_tables(db)
        reclaimed = reclaim_stale_processing(db, config.stale_timeout_minutes)
        ensure_destination_table(db, config)
        db.commit()

        files = sorted(
            os.path.join(watched_dir, name)
            for name in os.listdir(watched_dir)
            if os.path.isfile(os.path.join(watched_dir, name))
        )

        # Hash each File once per Run
        file_hashes = {path: compute_hash(path) for path in files}

        # Enqueue new Files as PENDING
        new_files = 0
        for path in files:
            content_hash = file_hashes[path]
            if find_file_by_hash(db, content_hash) is None:
                insert_pending(db, os.path.basename(path), content_hash)
                new_files += 1
        db.commit()

        # Process PENDING Files
        committed = failed = skipped = 0
        for path in files:
            content_hash = file_hashes[path]
            record = find_file_by_hash(db, content_hash)
            if record is None or record.state != "PENDING":
                continue

            # Tx 1: claim the File as PROCESSING (distributed lock)
            claim_processing(db, content_hash)
            db.commit()

            rows, error = load_file(db, config, path, content_hash)

            if error is None:
                # Tx 2: commit rows + COMMITTED marker together (ADR-0001)
                mark_committed(db, content_hash)
                db.commit()
                committed += 1
            else:
                db.rollback()  # undo any partial row inserts
                mark_failed(db, content_hash, error)
                db.commit()
                failed += 1

        return {
            "new_files": new_files,
            "committed": committed,
            "failed": failed,
            "skipped": skipped,
            "reclaimed": reclaimed,
        }
    finally:
        db.close()
