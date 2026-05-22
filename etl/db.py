import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from etl.config import PipelineConfig


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

    def close(self):
        self._conn.close()


# --- Audit table ---

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS etl_file_audit (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def create_audit_tables(db: Database) -> None:
    db.execute(_AUDIT_DDL)
    db.commit()


# --- File record ---

@dataclass
class FileRecord:
    id: int
    filename: str
    content_hash: str
    state: str
    attempt_count: int
    error_message: Optional[str]
    claimed_at: Optional[str]


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def find_file_by_hash(db: Database, content_hash: str) -> Optional[FileRecord]:
    cursor = db.execute(
        "SELECT id, filename, content_hash, state, attempt_count, error_message, claimed_at"
        " FROM etl_file_audit WHERE content_hash = ?",
        [content_hash],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return FileRecord(
        id=row[0], filename=row[1], content_hash=row[2], state=row[3],
        attempt_count=row[4], error_message=row[5], claimed_at=row[6],
    )


def insert_pending(db: Database, filename: str, content_hash: str) -> None:
    if find_file_by_hash(db, content_hash) is not None:
        return  # Content Hash already tracked — idempotent by design (ADR-0002)
    now = _now()
    db.execute(
        "INSERT INTO etl_file_audit (filename, content_hash, state, created_at, updated_at)"
        " VALUES (?, ?, 'PENDING', ?, ?)",
        [filename, content_hash, now, now],
    )


def claim_processing(db: Database, content_hash: str) -> None:
    now = _now()
    db.execute(
        "UPDATE etl_file_audit SET state='PROCESSING', claimed_at=?, updated_at=?"
        " WHERE content_hash=?",
        [now, now, content_hash],
    )


def mark_committed(db: Database, content_hash: str) -> None:
    db.execute(
        "UPDATE etl_file_audit SET state='COMMITTED', updated_at=? WHERE content_hash=?",
        [_now(), content_hash],
    )


def mark_failed(db: Database, content_hash: str, error: str) -> None:
    db.execute(
        "UPDATE etl_file_audit SET state='FAILED', error_message=?,"
        " attempt_count=attempt_count+1, updated_at=? WHERE content_hash=?",
        [error, _now(), content_hash],
    )


def reclaim_stale_processing(db: Database, stale_minutes: int) -> int:
    cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=stale_minutes)).isoformat()
    cursor = db.execute(
        "UPDATE etl_file_audit SET state='PENDING', attempt_count=attempt_count+1, updated_at=?"
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
        "SELECT filename, error_message FROM etl_file_audit"
        " WHERE state='FAILED' ORDER BY updated_at DESC LIMIT 10"
    )
    recent_failures = [{"filename": row[0], "error_message": row[1]} for row in cursor.fetchall()]

    return {**counts, "recent_failures": recent_failures}


# --- Destination table management ---

class SchemaError(Exception):
    pass


_TYPE_TO_SQL = {
    "string": "TEXT",
    "integer": "INTEGER",
    "float": "REAL",
    "date": "TEXT",
    "timestamp": "TEXT",
    "boolean": "INTEGER",
}


def ensure_destination_table(db: Database, config: PipelineConfig) -> None:
    existing = _get_existing_columns(db, config.dest_table)
    if existing is None:
        _create_destination_table(db, config)
        return
    mismatches = _detect_schema_mismatch(existing, config)
    if mismatches:
        raise SchemaError(
            f"Schema mismatch for table '{config.dest_table}':\n" + "\n".join(mismatches)
        )


def _get_existing_columns(db: Database, table: str) -> Optional[Dict[str, str]]:
    cursor = db.execute(f"PRAGMA table_info({table})")
    rows = cursor.fetchall()
    if not rows:
        return None  # Table does not exist
    return {row[1]: row[2].upper() for row in rows}


def _create_destination_table(db: Database, config: PipelineConfig) -> None:
    col_defs = ["_id INTEGER PRIMARY KEY"]
    for col in config.columns:
        sql_type = _TYPE_TO_SQL.get(col.type, "TEXT")
        col_defs.append(f"{col.dest} {sql_type}")
    col_defs.append("_source_file_hash TEXT NOT NULL")
    col_defs.append("_ingested_at TEXT NOT NULL")
    ddl = f"CREATE TABLE {config.dest_table} ({', '.join(col_defs)})"
    db.execute(ddl)


def _detect_schema_mismatch(existing: Dict[str, str], config: PipelineConfig) -> List[str]:
    required = {col.dest for col in config.columns} | {"_source_file_hash", "_ingested_at"}
    return [
        f"  Column '{name}' declared in pipeline.yaml but missing from table"
        for name in sorted(required)
        if name not in existing
    ]
