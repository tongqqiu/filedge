import base64

import pytest

from filedge.config import CdcConfig, ColumnMapping, EncryptConfig, HashConfig, PipelineConfig
from filedge.connectors.sqlite import SQLiteConnector
from filedge.loader import load_file


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="string", required=True),
        ],
        batch_size=2,
    )


@pytest.fixture
def connector(tmp_path, config):
    url = f"sqlite:///{tmp_path}/loader_test.db"
    c = SQLiteConnector(url=url, write_mode="append", batch_size=2)
    c.ensure_table(config)
    return c


def test_load_file_inserts_all_rows(connector, config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nfoo,bar\nbaz,qux\n")

    rows, error = load_file(connector, config, str(f), "testhash")
    assert error is None
    assert rows == 2

    conn = connector._get_conn()
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 2


def test_load_file_sets_provenance_columns(connector, config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nalpha,beta\n")

    load_file(connector, config, str(f), "myhash")

    conn = connector._get_conn()
    row = conn.execute("SELECT _source_file_hash, _ingested_at FROM items").fetchone()
    assert row[0] == "myhash"
    assert row[1] is not None


def test_load_file_returns_error_on_bad_row(connector, config, tmp_path):
    # Missing required column 'value' triggers TransformError → strict mode
    f = tmp_path / "bad.csv"
    f.write_text("name\nfoo\n")

    rows, error = load_file(connector, config, str(f), "badhash")
    assert error is not None
    assert "value" in error

    # Connector rolled back internally — no rows should be present
    conn = connector._get_conn()
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 0


def test_load_file_encrypts_columns_before_connector_write(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_KEY", base64.b64encode(b"e" * 32).decode("ascii"))
    config = PipelineConfig(
        format="csv",
        dest_table="customers",
        columns=[
            ColumnMapping(
                "ssn",
                "ssn_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:DATA_KEY"),
            )
        ],
    )
    c = SQLiteConnector(url=f"sqlite:///{tmp_path}/encrypted.db")
    c.ensure_table(config)
    f = tmp_path / "customers.csv"
    f.write_text("ssn\n123-45-6789\n")

    rows, error = load_file(c, config, str(f), "encryptedhash")

    assert error is None
    assert rows == 1
    row = c._get_conn().execute("SELECT ssn_ct FROM customers").fetchone()
    assert row[0] != "123-45-6789"
    assert base64.b64decode(row[0])[0] == 1
    c.close()


def test_load_file_hashes_columns_before_connector_write(tmp_path, monkeypatch):
    monkeypatch.setenv("JOIN_KEY", base64.b64encode(b"h" * 32).decode("ascii"))
    config = PipelineConfig(
        format="csv",
        dest_table="customers",
        columns=[
            ColumnMapping(
                "email",
                "email_join",
                "string",
                hash=HashConfig("hmac-sha256", "env:JOIN_KEY"),
            )
        ],
    )
    c = SQLiteConnector(url=f"sqlite:///{tmp_path}/hashed.db")
    c.ensure_table(config)
    f = tmp_path / "customers.csv"
    f.write_text("email\nperson@example.com\nperson@example.com\n")

    rows, error = load_file(c, config, str(f), "hashedhash")

    assert error is None
    assert rows == 2
    stored = [
        row[0]
        for row in c._get_conn().execute(
            "SELECT email_join FROM customers ORDER BY _id"
        ).fetchall()
    ]
    assert stored[0] == stored[1]
    assert stored[0] != "person@example.com"
    c.close()


def test_load_file_fails_before_connector_write_when_key_is_missing(tmp_path):
    config = PipelineConfig(
        format="csv",
        dest_table="customers",
        columns=[
            ColumnMapping(
                "ssn",
                "ssn_ct",
                "string",
                encrypt=EncryptConfig("aes-256-gcm", "env:MISSING_DATA_KEY"),
            )
        ],
    )
    c = SQLiteConnector(url=f"sqlite:///{tmp_path}/missing_key.db")
    c.ensure_table(config)
    f = tmp_path / "customers.csv"
    f.write_text("ssn\n123-45-6789\n")

    rows, error = load_file(c, config, str(f), "missingkeyhash")

    assert rows == 0
    assert error is not None
    assert "MISSING_DATA_KEY" in error
    assert c._get_conn().execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0
    c.close()


def test_load_file_streams_across_batch_boundary(connector, config, tmp_path):
    # batch_size=2, load 5 rows — exercises multiple batch flushes
    lines = ["name,value"] + [f"row{i},{i}" for i in range(5)]
    f = tmp_path / "data.csv"
    f.write_text("\n".join(lines) + "\n")

    rows, error = load_file(connector, config, str(f), "batchhash")
    assert error is None
    assert rows == 5

    conn = connector._get_conn()
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 5


def test_load_file_reports_rows_at_interval(connector, config, tmp_path):
    lines = ["name,value"] + [f"row{i},{i}" for i in range(5)]
    f = tmp_path / "data.csv"
    f.write_text("\n".join(lines) + "\n")
    events = []

    rows, error = load_file(
        connector,
        config,
        str(f),
        "progresshash",
        progress=events.append,
        row_report_interval=2,
    )

    assert error is None
    assert rows == 5
    assert [event.rows for event in events] == [2, 4]


def test_load_file_applies_cdc_rows(connector, tmp_path):
    config = PipelineConfig(
        format="ndjson",
        dest_table="items",
        write_mode="cdc",
        columns=[
            ColumnMapping("id", "id", "string", True),
            ColumnMapping("value", "value", "string", False),
            ColumnMapping("updated_at", "updated_at", "timestamp", True),
        ],
        cdc=CdcConfig(
            keys=["id"],
            operation_column="op",
            sequence_by="updated_at",
            operations={"insert": ["c"], "update": ["u"], "delete": ["d"]},
        ),
    )
    c = SQLiteConnector(
        url=f"sqlite:///{tmp_path}/loader_cdc.db", write_mode="cdc", batch_size=100
    )
    c.ensure_table(config)
    f = tmp_path / "changes.ndjson"
    f.write_text(
        '{"id":"1","value":"old","updated_at":"2026-05-01T00:00:00","op":"c"}\n'
        '{"id":"1","value":"new","updated_at":"2026-05-02T00:00:00","op":"u"}\n'
    )

    rows, error = load_file(c, config, str(f), "cdchash")

    assert error is None
    assert rows == 2
    row = c._get_conn().execute(
        "SELECT id, value, _source_file_hash FROM items"
    ).fetchone()
    assert row == ("1", "new", "cdchash")
    c.close()


def test_load_file_applies_cdc_with_renamed_key_column(tmp_path):
    config = PipelineConfig(
        format="ndjson",
        dest_table="items",
        write_mode="cdc",
        columns=[
            ColumnMapping("source_id", "id", "string", True),
            ColumnMapping("source_value", "value", "string", False),
            ColumnMapping("source_updated_at", "updated_at", "timestamp", True),
        ],
        cdc=CdcConfig(
            keys=["source_id"],
            operation_column="op",
            sequence_by="source_updated_at",
            operations={"insert": ["c"], "update": ["u"], "delete": ["d"]},
        ),
    )
    c = SQLiteConnector(
        url=f"sqlite:///{tmp_path}/loader_cdc_renamed.db",
        write_mode="cdc",
        batch_size=100,
    )
    c.ensure_table(config)
    f = tmp_path / "changes.ndjson"
    f.write_text(
        '{"source_id":"1","source_value":"new","source_updated_at":"2026-05-02T00:00:00","op":"c"}\n'
    )

    rows, error = load_file(c, config, str(f), "renamedhash")

    assert error is None
    assert rows == 1
    row = c._get_conn().execute("SELECT id, value FROM items").fetchone()
    assert row == ("1", "new")
    c.close()


def test_load_file_returns_error_on_unknown_cdc_operation(tmp_path):
    config = PipelineConfig(
        format="ndjson",
        dest_table="items",
        write_mode="cdc",
        columns=[
            ColumnMapping("id", "id", "string", True),
            ColumnMapping("value", "value", "string", False),
            ColumnMapping("updated_at", "updated_at", "timestamp", True),
        ],
        cdc=CdcConfig(
            keys=["id"],
            operation_column="op",
            sequence_by="updated_at",
            operations={"insert": ["c"], "update": ["u"], "delete": ["d"]},
        ),
    )
    c = SQLiteConnector(
        url=f"sqlite:///{tmp_path}/loader_cdc_bad_op.db",
        write_mode="cdc",
        batch_size=100,
    )
    c.ensure_table(config)
    f = tmp_path / "changes.ndjson"
    f.write_text(
        '{"id":"1","value":"bad","updated_at":"2026-05-02T00:00:00","op":"x"}\n'
    )

    rows, error = load_file(c, config, str(f), "badop")

    assert rows == 1
    assert error is not None
    assert "Unknown CDC operation: 'x'" in error
    assert c._get_conn().execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    c.close()
