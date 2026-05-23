from etl.config import load_config
from etl.connectors import get_connector
from etl.db import (
    Database,
    claim_processing,
    create_audit_tables,
    find_file_by_hash,
    insert_pending,
    mark_committed,
    mark_failed,
    reclaim_stale_processing,
    reset_eligible_failed,
)
from etl.filesystem import file_basename, get_filesystem, list_files
from etl.hashing import compute_hash
from etl.loader import load_file


def run_pipeline(watched_dir: str, config_path: str, audit_db_url: str) -> dict:
    config = load_config(config_path)
    db = Database(audit_db_url)
    connector = get_connector(config)
    fs, root = get_filesystem(watched_dir)

    try:
        create_audit_tables(db)

        retried = reset_eligible_failed(db, config.retry_cap)
        reclaimed = reclaim_stale_processing(db, config.stale_timeout_minutes)
        db.commit()

        connector.ensure_table(config)

        files = list_files(fs, root)
        file_hashes = {path: compute_hash(path, fs) for path in files}

        new_files = 0
        for path in files:
            content_hash = file_hashes[path]
            if find_file_by_hash(db, content_hash) is None:
                insert_pending(db, file_basename(path), content_hash)
                new_files += 1
        db.commit()

        committed = failed = skipped = 0
        for path in files:
            content_hash = file_hashes[path]
            record = find_file_by_hash(db, content_hash)
            if record is None or record.state != "PENDING":
                if record is not None and record.state == "FAILED":
                    skipped += 1
                continue

            claim_processing(db, content_hash)
            db.commit()

            rows, error = load_file(connector, config, path, content_hash, fs)

            if error is None:
                mark_committed(db, content_hash)
                db.commit()
                committed += 1
            else:
                mark_failed(db, content_hash, error)
                db.commit()
                failed += 1

        return {
            "new_files": new_files,
            "committed": committed,
            "failed": failed,
            "skipped": skipped,
            "reclaimed": reclaimed,
            "retried": retried,
        }
    finally:
        connector.close()
        db.close()
