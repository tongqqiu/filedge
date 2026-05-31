import pytest

from filedge.db import (
    Database,
    claim_processing,
    create_audit_tables,
    insert_pending,
    mark_committed,
    mark_failed,
)
from filedge.audit_records import export_records
from filedge.exporter import export_audit


@pytest.fixture
def audit_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/audit.db")
    create_audit_tables(db)
    return db


def test_export_audit_creates_output_file_and_returns_count(audit_db, tmp_path):
    insert_pending(audit_db, "orders.csv", "hash-a")
    claim_processing(audit_db, "hash-a")
    mark_committed(audit_db, "hash-a", row_count=100)
    audit_db.commit()

    output = tmp_path / "site" / "index.html"
    count = export_audit(audit_db, str(output))

    assert output.exists()
    assert count == 1


def test_export_html_contains_filenames(audit_db, tmp_path):
    insert_pending(audit_db, "sales_2026.csv", "hash-s")
    insert_pending(audit_db, "returns_2026.csv", "hash-r")
    mark_committed(audit_db, "hash-s", row_count=50)
    mark_failed(audit_db, "hash-r", "schema mismatch")
    audit_db.commit()

    output = tmp_path / "index.html"
    export_audit(audit_db, str(output))

    html = output.read_text()
    assert "sales_2026.csv" in html
    assert "returns_2026.csv" in html


def test_export_html_contains_states(audit_db, tmp_path):
    insert_pending(audit_db, "a.csv", "hash-ca")
    insert_pending(audit_db, "b.csv", "hash-fa")
    mark_committed(audit_db, "hash-ca", row_count=10)
    mark_failed(audit_db, "hash-fa", "bad row")
    audit_db.commit()

    output = tmp_path / "index.html"
    export_audit(audit_db, str(output))

    html = output.read_text()
    assert "COMMITTED" in html
    assert "FAILED" in html


def test_export_html_shows_row_count_and_null_placeholder(audit_db, tmp_path):
    insert_pending(audit_db, "known.csv", "hash-known")
    insert_pending(audit_db, "old.csv", "hash-old")
    mark_committed(audit_db, "hash-known", row_count=2500)
    mark_committed(audit_db, "hash-old", row_count=None)
    audit_db.commit()

    output = tmp_path / "index.html"
    export_audit(audit_db, str(output))

    html = output.read_text()
    assert "2,500" in html
    assert "—" in html


def test_export_html_lineage_sql_contains_hash_and_table(audit_db, tmp_path):
    insert_pending(audit_db, "txns.csv", "deadbeef1234")
    mark_committed(audit_db, "deadbeef1234", row_count=10)
    audit_db.commit()

    output = tmp_path / "index.html"
    export_audit(audit_db, str(output), dest_table="finance.transactions")

    html = output.read_text()
    assert "deadbeef1234" in html
    assert "finance.transactions" in html


def test_export_html_includes_title(audit_db, tmp_path):
    output = tmp_path / "index.html"
    export_audit(audit_db, str(output), title="KYC Documents Pipeline")

    html = output.read_text()
    assert "KYC Documents Pipeline" in html


def test_export_record_carries_quarantine_fields(audit_db):
    insert_pending(audit_db, "partner.csv", "hash-q")
    claim_processing(audit_db, "hash-q")
    mark_committed(
        audit_db,
        "hash-q",
        row_count=98,
        quarantined_row_count=2,
        quarantine_path="/q/partner.hash-q.quarantine.ndjson",
    )
    audit_db.commit()

    records = export_records(audit_db)

    assert len(records) == 1
    assert records[0].quarantined_row_count == 2
    assert records[0].quarantine_path == "/q/partner.hash-q.quarantine.ndjson"


def test_export_html_distinguishes_partial_commit_from_clean(audit_db, tmp_path):
    insert_pending(audit_db, "partner.csv", "hash-partial")
    insert_pending(audit_db, "clean.csv", "hash-clean")
    mark_committed(
        audit_db,
        "hash-partial",
        row_count=98,
        quarantined_row_count=2,
        quarantine_path="/q/partner.hash-partial.quarantine.ndjson",
    )
    mark_committed(audit_db, "hash-clean", row_count=100)
    audit_db.commit()

    output = tmp_path / "index.html"
    export_audit(audit_db, str(output))

    html = output.read_text()
    # The partial commit is visibly flagged with its quarantined count and sidecar path.
    assert "quarantined" in html
    assert "/q/partner.hash-partial.quarantine.ndjson" in html


def test_export_html_clean_commit_has_no_quarantine_indicator(audit_db, tmp_path):
    insert_pending(audit_db, "clean.csv", "hash-clean")
    mark_committed(audit_db, "hash-clean", row_count=100)
    audit_db.commit()

    output = tmp_path / "index.html"
    export_audit(audit_db, str(output))

    html = output.read_text()
    assert "quarantined" not in html.lower()
