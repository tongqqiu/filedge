import datetime
import os
from typing import Optional

from filedge.db import Database


def export_audit(
    db: Database,
    output_path: str,
    title: Optional[str] = None,
    dest_table: Optional[str] = None,
) -> int:
    """Render the Audit DB to a self-contained HTML file. Returns the record count."""
    from jinja2 import Environment, FileSystemLoader

    cursor = db.execute(
        "SELECT id, filename, source_dir, content_hash, state, attempt_count,"
        " error_message, worker_id, claimed_at, row_count, updated_at"
        " FROM etl_file_audit ORDER BY updated_at DESC"
    )
    rows = cursor.fetchall()

    records = [
        {
            "id": r[0],
            "filename": r[1],
            "source_dir": r[2],
            "content_hash": r[3],
            "state": r[4],
            "attempt_count": r[5],
            "error_message": r[6],
            "worker_id": r[7],
            "claimed_at": r[8],
            "row_count": r[9],
            "updated_at": r[10],
        }
        for r in rows
    ]

    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
    template = env.get_template("audit_export.html.j2")

    html = template.render(
        records=records,
        title=title,
        dest_table=dest_table,
        generated_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return len(records)
