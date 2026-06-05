import datetime
import os
from typing import Optional

from filedge.audit_records import export_records, status_summary
from filedge.db import Database

_STATES = ("COMMITTED", "PROCESSING", "PENDING", "FAILED")


def _summary(db: Database, records: list) -> dict:
    """At-a-glance counts for the export overview strip.

    Reuses the same `status_summary` the Operator CLI prints so the served page
    and `filedge status` never disagree on the numbers.
    """
    summary = status_summary(db)
    return {
        "total": len(records),
        "by_state": {state: summary.get(state, 0) for state in _STATES},
        "total_rows": sum(r.row_count or 0 for r in records),
        "quarantined_rows": summary.get("quarantined_rows", 0),
    }


def export_audit(
    db: Database,
    output_path: str,
    title: Optional[str] = None,
    dest_table: Optional[str] = None,
) -> int:
    """Render the Audit DB to a self-contained HTML file. Returns the record count."""
    from jinja2 import Environment, FileSystemLoader

    records = export_records(db)

    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
    template = env.get_template("audit_export.html.j2")

    html = template.render(
        records=records,
        title=title,
        dest_table=dest_table,
        summary=_summary(db, records),
        generated_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return len(records)
