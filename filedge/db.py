import datetime
from dataclasses import dataclass
from typing import Dict, Literal, Optional


# --- Database wrapper ---

class Database:
    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            import sqlite3
            path = url[len("sqlite:///"):]
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._placeholder = "?"
        else:
            import psycopg2
            self._conn = psycopg2.connect(url)
            self._placeholder = "%s"

    def _sql(self, sql: str) -> str:
        if self._placeholder == "%s":
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params=None):
        cursor = self._conn.cursor()
        cursor.execute(self._sql(sql), params or [])
        return cursor

    def executemany(self, sql: str, params_list):
        cursor = self._conn.cursor()
        cursor.executemany(self._sql(sql), params_list)
        return cursor

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def dialect(self) -> Literal["sqlite", "postgres"]:
        return "postgres" if self._placeholder == "%s" else "sqlite"

    def close(self):
        self._conn.close()


# --- Audit table ---

_AUDIT_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS etl_file_audit (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    source_dir TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    worker_id TEXT,
    run_id TEXT,
    row_count INTEGER,
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_type TEXT,
    source_name TEXT,
    producer TEXT,
    external_run_id TEXT,
    manifest_payload TEXT,
    manifest_version TEXT,
    manifest_started_at TEXT,
    manifest_finished_at TEXT,
    manifest_record_count INTEGER,
    source_range TEXT
)
"""

_AUDIT_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS etl_file_audit (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    source_dir TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    worker_id TEXT,
    run_id TEXT,
    row_count INTEGER,
    claimed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    source_type TEXT,
    source_name TEXT,
    producer TEXT,
    external_run_id TEXT,
    manifest_payload TEXT,
    manifest_version TEXT,
    manifest_started_at TIMESTAMP WITH TIME ZONE,
    manifest_finished_at TIMESTAMP WITH TIME ZONE,
    manifest_record_count BIGINT,
    source_range TEXT
)
"""

_SOURCE_MANIFEST_TEXT_COLUMNS = (
    "source_type", "source_name", "producer", "external_run_id", "manifest_payload",
    "manifest_version", "manifest_started_at", "manifest_finished_at", "source_range",
)
_SOURCE_MANIFEST_INT_COLUMNS = ("manifest_record_count",)


def create_audit_tables(db: Database) -> None:
    ddl = _AUDIT_DDL_POSTGRES if db.dialect() == "postgres" else _AUDIT_DDL_SQLITE
    db.execute(ddl)
    _ensure_audit_columns(db)
    db.commit()


def _ensure_audit_columns(db: Database) -> None:
    if db.dialect() == "postgres":
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN IF NOT EXISTS worker_id TEXT")
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN IF NOT EXISTS source_dir TEXT")
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN IF NOT EXISTS run_id TEXT")
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN IF NOT EXISTS row_count INTEGER")  # pragma: no cover
        for col in _SOURCE_MANIFEST_TEXT_COLUMNS:
            db.execute(f"ALTER TABLE etl_file_audit ADD COLUMN IF NOT EXISTS {col} TEXT")
        for col in _SOURCE_MANIFEST_INT_COLUMNS:
            db.execute(f"ALTER TABLE etl_file_audit ADD COLUMN IF NOT EXISTS {col} BIGINT")
        return

    cursor = db.execute("PRAGMA table_info(etl_file_audit)")
    existing = {row[1] for row in cursor.fetchall()}
    if "worker_id" not in existing:
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN worker_id TEXT")
    if "source_dir" not in existing:
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN source_dir TEXT")
    if "run_id" not in existing:
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN run_id TEXT")
    if "row_count" not in existing:
        db.execute("ALTER TABLE etl_file_audit ADD COLUMN row_count INTEGER")
    for col in _SOURCE_MANIFEST_TEXT_COLUMNS:
        if col not in existing:
            db.execute(f"ALTER TABLE etl_file_audit ADD COLUMN {col} TEXT")
    for col in _SOURCE_MANIFEST_INT_COLUMNS:
        if col not in existing:
            db.execute(f"ALTER TABLE etl_file_audit ADD COLUMN {col} INTEGER")


# --- File record ---

@dataclass
class FileRecord:
    id: int
    filename: str
    source_dir: Optional[str]
    content_hash: str
    state: str
    attempt_count: int
    error_message: Optional[str]
    worker_id: Optional[str]
    claimed_at: Optional[str]
    row_count: Optional[int] = None
    source_type: Optional[str] = None
    source_name: Optional[str] = None
    producer: Optional[str] = None
    external_run_id: Optional[str] = None
    manifest_payload: Optional[str] = None
    manifest_version: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    record_count: Optional[int] = None
    source_range: Optional[dict] = None


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def get_hash_states(db: Database, hashes: list) -> Dict[str, str]:
    """Return {content_hash: state} for all hashes that exist in the audit table.

    Chunked to stay within SQLite's variable limit (999). Safe for any list size.
    """
    if not hashes:
        return {}
    result: Dict[str, str] = {}
    chunk_size = 500
    for i in range(0, len(hashes), chunk_size):
        chunk = hashes[i : i + chunk_size]
        placeholders = ", ".join(["?"] * len(chunk))
        cursor = db.execute(
            f"SELECT content_hash, state FROM etl_file_audit WHERE content_hash IN ({placeholders})",
            chunk,
        )
        result.update({row[0]: row[1] for row in cursor.fetchall()})
    return result


def find_file_by_hash(db: Database, content_hash: str) -> Optional[FileRecord]:
    cursor = db.execute(
        "SELECT id, filename, source_dir, content_hash, state, attempt_count, error_message, worker_id, claimed_at, row_count,"
        " source_type, source_name, producer, external_run_id, manifest_payload,"
        " manifest_version, manifest_started_at, manifest_finished_at, manifest_record_count, source_range"
        " FROM etl_file_audit WHERE content_hash = ?",
        [content_hash],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    import json as _json
    raw_source_range = row[19]
    source_range = _json.loads(raw_source_range) if raw_source_range else None
    return FileRecord(
        id=row[0], filename=row[1], source_dir=row[2], content_hash=row[3], state=row[4],
        attempt_count=row[5], error_message=row[6], worker_id=row[7], claimed_at=row[8],
        row_count=row[9],
        source_type=row[10], source_name=row[11], producer=row[12],
        external_run_id=row[13], manifest_payload=row[14],
        manifest_version=row[15],
        started_at=row[16] if row[16] is None or isinstance(row[16], str) else row[16].isoformat(),
        finished_at=row[17] if row[17] is None or isinstance(row[17], str) else row[17].isoformat(),
        record_count=row[18],
        source_range=source_range,
    )


def insert_pending(
    db: Database,
    filename: str,
    content_hash: str,
    source_dir: Optional[str] = None,
    source_metadata=None,
) -> None:
    if find_file_by_hash(db, content_hash) is not None:
        return  # Content Hash already tracked — idempotent by design (ADR-0002)
    now = _now()
    if source_metadata is None:
        db.execute(
            "INSERT INTO etl_file_audit (filename, source_dir, content_hash, state, created_at, updated_at)"
            " VALUES (?, ?, ?, 'PENDING', ?, ?)",
            [filename, source_dir, content_hash, now, now],
        )
        return
    import json as _json
    range_blob = _json.dumps(source_metadata.source_range) if source_metadata.source_range else None
    db.execute(
        "INSERT INTO etl_file_audit"
        " (filename, source_dir, content_hash, state, created_at, updated_at,"
        "  source_type, source_name, producer, external_run_id, manifest_payload,"
        "  manifest_version, manifest_started_at, manifest_finished_at, manifest_record_count, source_range)"
        " VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            filename, source_dir, content_hash, now, now,
            source_metadata.source_type, source_metadata.source_name,
            source_metadata.producer, source_metadata.external_run_id,
            source_metadata.raw_payload,
            source_metadata.manifest_version,
            source_metadata.started_at,
            source_metadata.finished_at,
            source_metadata.record_count,
            range_blob,
        ],
    )


def claim_processing(
    db: Database,
    content_hash: str,
    worker_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> bool:
    now = _now()
    cursor = db.execute(
        "UPDATE etl_file_audit"
        " SET state='PROCESSING', worker_id=?, run_id=?, claimed_at=?, updated_at=?"
        " WHERE content_hash=? AND state='PENDING'",
        [worker_id, run_id, now, now, content_hash],
    )
    return cursor.rowcount == 1


def mark_committed(db: Database, content_hash: str, row_count: Optional[int] = None) -> None:
    db.execute(
        "UPDATE etl_file_audit SET state='COMMITTED', worker_id=NULL, row_count=?, updated_at=? WHERE content_hash=?",
        [row_count, _now(), content_hash],
    )


def mark_failed(db: Database, content_hash: str, error: str) -> None:
    db.execute(
        "UPDATE etl_file_audit SET state='FAILED', error_message=?,"
        " worker_id=NULL, attempt_count=attempt_count+1, updated_at=? WHERE content_hash=?",
        [error, _now(), content_hash],
    )


def reset_eligible_failed(db: Database, retry_cap: int) -> int:
    """Reset FAILED files with attempt_count < retry_cap back to PENDING for retry."""
    cursor = db.execute(
        "UPDATE etl_file_audit SET state='PENDING', worker_id=NULL, updated_at=?"
        " WHERE state='FAILED' AND attempt_count < ?",
        [_now(), retry_cap],
    )
    return cursor.rowcount


def reclaim_stale_processing(db: Database, stale_minutes: int) -> int:
    cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=stale_minutes)).isoformat()
    cursor = db.execute(
        "UPDATE etl_file_audit SET state='PENDING', worker_id=NULL,"
        " attempt_count=attempt_count+1, updated_at=?"
        " WHERE state='PROCESSING' AND claimed_at < ?",
        [_now(), cutoff],
    )
    return cursor.rowcount


def get_status_summary(db: Database) -> dict:
    cursor = db.execute("SELECT state, COUNT(*) FROM etl_file_audit GROUP BY state")
    counts: Dict[str, int] = {"PENDING": 0, "PROCESSING": 0, "COMMITTED": 0, "FAILED": 0}
    for row in cursor.fetchall():
        counts[row[0]] = row[1]

    cursor = db.execute(
        "SELECT filename, content_hash, error_message,"
        " source_type, source_name, producer, external_run_id"
        " FROM etl_file_audit"
        " WHERE state='FAILED' ORDER BY updated_at DESC LIMIT 10"
    )
    recent_failures = [
        {
            "filename": row[0],
            "content_hash": row[1],
            "error_message": row[2],
            "source_type": row[3],
            "source_name": row[4],
            "producer": row[5],
            "external_run_id": row[6],
        }
        for row in cursor.fetchall()
    ]

    return {**counts, "recent_failures": recent_failures}


def find_files_by_filename(db: Database, filename: str) -> list["FileRecord"]:
    """Return every audit record matching filename, regardless of state."""
    cursor = db.execute(
        "SELECT content_hash FROM etl_file_audit WHERE filename = ?",
        [filename],
    )
    hashes = [r[0] for r in cursor.fetchall()]
    records = [find_file_by_hash(db, h) for h in hashes]
    return [r for r in records if r is not None]


def find_terminal_failed_by_filename(
    db: Database, filename: str, retry_cap: int
) -> list["FileRecord"]:
    """Return all terminal-FAILED records matching filename (attempt_count >= retry_cap)."""
    cursor = db.execute(
        "SELECT id, filename, source_dir, content_hash, state, attempt_count, error_message, worker_id, claimed_at"
        " FROM etl_file_audit WHERE filename=? AND state='FAILED' AND attempt_count >= ?",
        [filename, retry_cap],
    )
    return [
        FileRecord(
            id=r[0], filename=r[1], source_dir=r[2], content_hash=r[3], state=r[4],
            attempt_count=r[5], error_message=r[6], worker_id=r[7], claimed_at=r[8],
        )
        for r in cursor.fetchall()
    ]


def list_terminal_failed(db: Database, retry_cap: int) -> list["FileRecord"]:
    """Return all terminal-FAILED records ordered by most recently updated."""
    cursor = db.execute(
        "SELECT id, filename, source_dir, content_hash, state, attempt_count, error_message, worker_id, claimed_at"
        " FROM etl_file_audit WHERE state='FAILED' AND attempt_count >= ?"
        " ORDER BY updated_at DESC",
        [retry_cap],
    )
    return [
        FileRecord(
            id=r[0], filename=r[1], source_dir=r[2], content_hash=r[3], state=r[4],
            attempt_count=r[5], error_message=r[6], worker_id=r[7], claimed_at=r[8],
        )
        for r in cursor.fetchall()
    ]


def requeue_by_hash(db: Database, content_hash: str) -> None:
    """Reset a single file to PENDING with a fresh retry budget."""
    db.execute(
        "UPDATE etl_file_audit SET state='PENDING', attempt_count=0, error_message=NULL,"
        " worker_id=NULL, updated_at=? WHERE content_hash=?",
        [_now(), content_hash],
    )


def requeue_all_terminal_failed(db: Database, retry_cap: int) -> int:
    """Reset all terminal-FAILED files to PENDING. Returns count reset."""
    cursor = db.execute(
        "UPDATE etl_file_audit SET state='PENDING', attempt_count=0, error_message=NULL,"
        " worker_id=NULL, updated_at=? WHERE state='FAILED' AND attempt_count >= ?",
        [_now(), retry_cap],
    )
    return cursor.rowcount
