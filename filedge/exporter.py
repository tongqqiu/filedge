import datetime
import os
from typing import Optional

from filedge.audit_records import export_records
from filedge.db import Database


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
        generated_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return len(records)
