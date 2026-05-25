from dataclasses import dataclass
from typing import Dict, Optional

from filedge.db import Database, FileRecord, find_file_by_hash, find_files_by_filename


@dataclass(frozen=True)
class AuditExportRecord:
    id: int
    filename: str
    source_dir: Optional[str]
    content_hash: str
    state: str
    attempt_count: int
    error_message: Optional[str]
    worker_id: Optional[str]
    claimed_at: Optional[str]
    row_count: Optional[int]
    updated_at: Optional[str]


@dataclass(frozen=True)
class LineageFound:
    record: FileRecord
    run_id: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


@dataclass(frozen=True)
class LineageMissing:
    identifier: str


@dataclass(frozen=True)
class LineageAmbiguous:
    identifier: str
    matches: list[FileRecord]


def status_summary(db: Database) -> dict:
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


def export_records(db: Database) -> list[AuditExportRecord]:
    cursor = db.execute(
        "SELECT id, filename, source_dir, content_hash, state, attempt_count,"
        " error_message, worker_id, claimed_at, row_count, updated_at"
        " FROM etl_file_audit ORDER BY updated_at DESC"
    )
    return [
        AuditExportRecord(
            id=row[0],
            filename=row[1],
            source_dir=row[2],
            content_hash=row[3],
            state=row[4],
            attempt_count=row[5],
            error_message=row[6],
            worker_id=row[7],
            claimed_at=row[8],
            row_count=row[9],
            updated_at=row[10],
        )
        for row in cursor.fetchall()
    ]


def lineage_record(
    db: Database,
    identifier: str,
) -> LineageFound | LineageMissing | LineageAmbiguous:
    by_hash = find_file_by_hash(db, identifier)
    if by_hash is not None:
        return _lineage_found(db, by_hash)

    matches = find_files_by_filename(db, identifier)
    if not matches:
        return LineageMissing(identifier=identifier)
    if len(matches) > 1:
        return LineageAmbiguous(identifier=identifier, matches=matches)
    return _lineage_found(db, matches[0])


def _lineage_found(db: Database, record: FileRecord) -> LineageFound:
    cursor = db.execute(
        "SELECT run_id, created_at, updated_at FROM etl_file_audit WHERE content_hash = ?",
        [record.content_hash],
    )
    row = cursor.fetchone()
    run_id, created_at, updated_at = row if row is not None else (None, None, None)
    return LineageFound(
        record=record,
        run_id=run_id,
        created_at=created_at,
        updated_at=updated_at,
    )
