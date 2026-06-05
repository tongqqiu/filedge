import http.server
import threading
import urllib.request

import pytest

from filedge.db import Database, create_audit_tables, insert_pending, mark_committed
from filedge.serve import INDEX_NAME, build_site, make_handler


@pytest.fixture
def audit_db_url(tmp_path):
    url = f"sqlite:///{tmp_path}/audit.db"
    db = Database(url)
    create_audit_tables(db)
    insert_pending(db, "orders.csv", "hash-a")
    mark_committed(db, "hash-a", row_count=100)
    db.commit()
    db.close()
    return url


def test_build_site_writes_index_and_returns_count(audit_db_url, tmp_path):
    serve_dir = tmp_path / "site"
    serve_dir.mkdir()

    count = build_site(audit_db_url, str(serve_dir), title="Orders")

    index = serve_dir / INDEX_NAME
    assert index.exists()
    assert count == 1
    assert "orders.csv" in index.read_text()
    assert "Orders" in index.read_text()


def _serve_in_thread(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server, path="/"):
    host, port = server.server_address
    with urllib.request.urlopen(f"http://{host}:{port}{path}") as resp:
        return resp.status, resp.read().decode()


def test_handler_serves_index(audit_db_url, tmp_path):
    serve_dir = tmp_path / "site"
    serve_dir.mkdir()
    handler = make_handler(audit_db_url, str(serve_dir), title="Orders")

    server, thread = _serve_in_thread(handler)
    try:
        status, body = _get(server, "/")
        assert status == 200
        assert "orders.csv" in body
    finally:
        server.shutdown()
        server.server_close()


def test_handler_regenerates_from_db_on_each_request(audit_db_url, tmp_path):
    serve_dir = tmp_path / "site"
    serve_dir.mkdir()
    handler = make_handler(audit_db_url, str(serve_dir))

    server, thread = _serve_in_thread(handler)
    try:
        _, first = _get(server, "/")
        assert "returns.csv" not in first

        # A concurrent run commits another File; serving it again reflects it.
        db = Database(audit_db_url)
        insert_pending(db, "returns.csv", "hash-b")
        mark_committed(db, "hash-b", row_count=5)
        db.commit()
        db.close()

        _, second = _get(server, "/")
        assert "returns.csv" in second
    finally:
        server.shutdown()
        server.server_close()
