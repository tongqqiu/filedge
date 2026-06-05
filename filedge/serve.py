"""Serve the read-only Audit Export locally over HTTP.

This is the `dbt docs serve` analogue: it does not introduce a stateful backend
or any control surface. It regenerates the same static Audit Export
(`filedge export-audit`) and serves it on localhost so an operator can browse
audit evidence in a browser without first picking an output path. Every request
only ever *reads* the Audit DB; all state-changing operations stay in the
Operator CLI, exactly as for the on-disk export.

The index page is regenerated on each request from the Audit DB so a long-lived
`filedge serve` reflects files committed by a concurrent `filedge run` without a
restart. A fresh `Database` is opened per request, keeping SQLite access on the
serving thread.
"""

import functools
import http.server
import os
import tempfile
from typing import Optional

from filedge.db import Database, create_audit_tables
from filedge.exporter import export_audit

INDEX_NAME = "index.html"


def build_site(
    audit_db_url: str,
    serve_dir: str,
    *,
    title: Optional[str] = None,
    dest_table: Optional[str] = None,
) -> int:
    """Render the Audit Export into ``serve_dir/index.html``. Returns record count."""
    db = Database(audit_db_url)
    try:
        create_audit_tables(db)
        return export_audit(
            db,
            os.path.join(serve_dir, INDEX_NAME),
            title=title,
            dest_table=dest_table,
        )
    finally:
        db.close()


class _AuditHandler(http.server.SimpleHTTPRequestHandler):
    """Static file handler that regenerates the index from the Audit DB on load."""

    audit_db_url: str = ""
    title: Optional[str] = None
    dest_table: Optional[str] = None

    def do_GET(self):  # noqa: N802 (stdlib casing)
        if self.path in ("/", "/" + INDEX_NAME):
            build_site(
                self.audit_db_url,
                self.directory,
                title=self.title,
                dest_table=self.dest_table,
            )
        super().do_GET()

    def log_message(self, *args):  # silence default per-request stderr logging
        pass


def make_handler(
    audit_db_url: str,
    serve_dir: str,
    *,
    title: Optional[str] = None,
    dest_table: Optional[str] = None,
):
    """Build a request handler bound to one Audit DB and serving directory."""
    bound = type(
        "BoundAuditHandler",
        (_AuditHandler,),
        {"audit_db_url": audit_db_url, "title": title, "dest_table": dest_table},
    )
    return functools.partial(bound, directory=serve_dir)


def serve_audit(
    audit_db_url: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    title: Optional[str] = None,
    dest_table: Optional[str] = None,
    open_browser: bool = True,
    log=print,
) -> None:
    """Generate and serve the Audit Export, blocking until interrupted."""
    import webbrowser

    serve_dir = tempfile.mkdtemp(prefix="filedge-serve-")
    count = build_site(audit_db_url, serve_dir, title=title, dest_table=dest_table)

    handler = make_handler(
        audit_db_url, serve_dir, title=title, dest_table=dest_table
    )
    server = http.server.ThreadingHTTPServer((host, port), handler)
    bound_host, bound_port = server.server_address[0], server.server_address[1]
    url = f"http://{bound_host}:{bound_port}/"

    log(f"Serving Audit Export ({count} files) at {url}")
    log("Read-only — all state-changing operations stay in the Operator CLI.")
    log("Press Ctrl-C to stop.")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\nStopping.")
    finally:
        server.shutdown()
        server.server_close()
