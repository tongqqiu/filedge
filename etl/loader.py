import datetime
from typing import Optional, Tuple

from etl.config import PipelineConfig
from etl.db import Database
from etl.parser import get_parser
from etl.transform import TransformError, transform_row


def load_file(
    db: Database,
    config: PipelineConfig,
    file_path: str,
    file_hash: str,
) -> Tuple[int, Optional[str]]:
    """
    Stream file through parser + transform and insert rows in batches.

    Does NOT commit — caller commits both the inserted rows and the COMMITTED audit
    marker in a single db.commit(), implementing the single-transaction Commit (ADR-0001).

    Returns (rows_loaded, error_message). error_message is None on success.
    On error, rows already inserted remain in the open transaction for the caller to rollback.
    """
    parser = get_parser(config.format)
    dest_cols = [col.dest for col in config.columns] + ["_source_file_hash", "_ingested_at"]
    placeholders = ", ".join(["?"] * len(dest_cols))
    insert_sql = f"INSERT INTO {config.dest_table} ({', '.join(dest_cols)}) VALUES ({placeholders})"

    batch = []
    rows_loaded = 0
    ingested_at = datetime.datetime.now(datetime.UTC).isoformat()

    try:
        for raw_row in parser.parse(file_path):
            transformed = transform_row(raw_row, config.columns)
            values = [transformed[col.dest] for col in config.columns] + [file_hash, ingested_at]
            batch.append(values)
            if len(batch) >= config.batch_size:
                db.executemany(insert_sql, batch)
                rows_loaded += len(batch)
                batch = []
        if batch:
            db.executemany(insert_sql, batch)
            rows_loaded += len(batch)
        return rows_loaded, None
    except TransformError as e:
        return rows_loaded, str(e)
    except Exception as e:
        return rows_loaded, f"Unexpected error: {e}"
