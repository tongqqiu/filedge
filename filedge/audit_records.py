from dataclasses import dataclass
from typing import Dict, Optional

from filedge.db import (
    Database,
    FileRecord,
    find_file_by_hash,
    find_files_by_filename,
    find_terminal_failed_by_filename,
    is_terminal_failed,
    requeue_by_hash,
)


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
    quarantined_row_count: Optional[int]
    quarantine_path: Optional[str]


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

    cursor = db.execute(
        "SELECT COALESCE(SUM(quarantined_row_count), 0) FROM etl_file_audit"
    )
    quarantined_rows = cursor.fetchone()[0] or 0

    return {**counts, "recent_failures": recent_failures, "quarantined_rows": quarantined_rows}


def export_records(db: Database) -> list[AuditExportRecord]:
    cursor = db.execute(
        "SELECT id, filename, source_dir, content_hash, state, attempt_count,"
        " error_message, worker_id, claimed_at, row_count, updated_at,"
        " quarantined_row_count, quarantine_path"
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
            quarantined_row_count=row[11],
            quarantine_path=row[12],
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


@dataclass(frozen=True)
class Requeued:
    record: FileRecord


@dataclass(frozen=True)
class RequeueNotFound:
    target: str


@dataclass(frozen=True)
class RequeueNotEligible:
    record: FileRecord


@dataclass(frozen=True)
class RequeueAmbiguous:
    matches: list[FileRecord]


RequeueOutcome = Requeued | RequeueNotFound | RequeueNotEligible | RequeueAmbiguous


def requeue_file(
    db: Database,
    *,
    retry_cap: int,
    content_hash: Optional[str] = None,
    filename: Optional[str] = None,
) -> RequeueOutcome:
    """Resolve a single requeue target and reset it, or report why it cannot be.

    Owns the requeue decision: identity resolution (by Content Hash or filename),
    terminal-FAILED eligibility, and duplicate-filename disambiguation. The caller
    renders the outcome and never re-derives eligibility. Resolution by hash is
    exact; resolution by filename considers only terminal-FAILED matches so a
    re-dropped duplicate filename in another state is ignored.
    """
    if content_hash is not None:
        record = find_file_by_hash(db, content_hash)
        if record is None:
            return RequeueNotFound(target=content_hash)
        if not is_terminal_failed(record, retry_cap):
            return RequeueNotEligible(record=record)
        requeue_by_hash(db, content_hash)
        db.commit()
        return Requeued(record=record)

    matches = find_terminal_failed_by_filename(db, filename, retry_cap)
    if not matches:
        return RequeueNotFound(target=filename)
    if len(matches) > 1:
        return RequeueAmbiguous(matches=matches)
    record = matches[0]
    requeue_by_hash(db, record.content_hash)
    db.commit()
    return Requeued(record=record)


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
