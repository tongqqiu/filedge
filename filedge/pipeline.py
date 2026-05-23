from filedge.config import load_config
from filedge.connectors import get_connector
from filedge.db import (
    Database,
    claim_pending_file,
    create_audit_tables,
    discover_file,
    finish_file,
    find_file_by_hash,
    prepare_run,
)
from filedge.filesystem import file_basename, get_filesystem, list_files
from filedge.hashing import compute_hash
from filedge.loader import load_file


def run_pipeline(watched_dir: str, config_path: str, audit_db_url: str) -> dict:
    config = load_config(config_path)
    db = Database(audit_db_url)
    connector = get_connector(config)
    fs, root = get_filesystem(watched_dir)

    try:
        create_audit_tables(db)

        preparation = prepare_run(db, config.retry_cap, config.stale_timeout_minutes)
        db.commit()

        connector.ensure_table(config)

        files = list_files(fs, root)
        file_hashes = {path: compute_hash(path, fs) for path in files}

        new_files = 0
        for path in files:
            content_hash = file_hashes[path]
            if discover_file(db, file_basename(path), content_hash):
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

            claimed = claim_pending_file(db, content_hash)
            db.commit()
            if not claimed:
                continue

            rows, error = load_file(connector, config, path, content_hash, fs)

            finish_file(db, content_hash, error=error)
            db.commit()
            if error is None:
                committed += 1
            else:
                failed += 1

        return {
            "new_files": new_files,
            "committed": committed,
            "failed": failed,
            "skipped": skipped,
            "reclaimed": preparation.reclaimed,
            "retried": preparation.retried,
        }
    finally:
        connector.close()
        db.close()
