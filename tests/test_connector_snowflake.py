"""Unit tests for the Snowflake connector: SQL generation and idempotency, driven
by a fake `snowflake.connector` (no warehouse). A gated end-to-end round trip
lives in test_connector_snowflake_integration.py.
"""

import sys
import types

import pytest

from filedge.config import CdcConfig, ColumnMapping, PipelineConfig
from filedge.connectors import SchemaError


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append(sql)
        self.conn.executed.append((sql, params))
        if self.conn.fail_execute_on and self.conn.fail_execute_on in sql:
            raise RuntimeError("execute failed")
        if "information_schema.columns" in sql:
            self._result = self.conn.existing_columns
        elif sql.strip().upper().startswith("SELECT 1"):
            self._result = [(1,)]
        else:
            self._result = []

    def executemany(self, sql, seq):
        self.conn.executemany_calls.append((sql, [list(v) for v in seq]))

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.executed = []
        self.executemany_calls = []
        self.existing_columns = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False
        self.fail_executemany = False
        self.fail_execute_on = None
        self.connect_kwargs = []

    def cursor(self):
        cur = FakeCursor(self)
        if self.fail_executemany:
            def boom(sql, seq):
                raise RuntimeError("insert failed")
            cur.executemany = boom
        return cur

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


@pytest.fixture
def fake_snowflake(monkeypatch):
    conn = FakeConnection()

    def _connect(**kw):
        conn.connect_kwargs.append(kw)
        return conn

    connector_mod = types.SimpleNamespace(connect=_connect)
    snowflake_mod = types.SimpleNamespace(connector=connector_mod)
    monkeypatch.setitem(sys.modules, "snowflake", snowflake_mod)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector_mod)
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
    return conn


def _config(**overrides):
    kwargs = dict(
        format="csv",
        dest_table="orders",
        columns=[
            ColumnMapping(source="name", dest="name", type="string"),
            ColumnMapping(source="amount", dest="amount", type="float"),
        ],
    )
    kwargs.update(overrides)
    return PipelineConfig(**kwargs)


def _connector(write_mode="append", **overrides):
    from filedge.connectors.snowflake import SnowflakeConnector

    kwargs = dict(
        account="acct", user="u", warehouse="wh", database="db", schema="public",
        write_mode=write_mode, batch_size=2,
    )
    kwargs.update(overrides)
    return SnowflakeConnector(**kwargs)


# --- construction --------------------------------------------------------------

def test_requires_connection_fields(fake_snowflake):
    from filedge.connectors.snowflake import SnowflakeConnector

    with pytest.raises(ValueError, match="database"):
        SnowflakeConnector(account="a", user="u", warehouse="w", schema="public")


def test_requires_a_credential(fake_snowflake, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)
    monkeypatch.delenv("SNOWFLAKE_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(ValueError, match="SNOWFLAKE_PRIVATE_KEY_PATH"):
        _connector()


def test_password_auth_used_when_no_key(fake_snowflake):
    # The fixture sets SNOWFLAKE_PASSWORD and no key path.
    _connector()
    kw = fake_snowflake.connect_kwargs[0]
    assert kw["password"] == "secret"
    assert "private_key_file" not in kw


def test_key_pair_auth_is_preferred_over_password(fake_snowflake, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", "/keys/rsa_key.p8")
    _connector()  # password is also set by the fixture
    kw = fake_snowflake.connect_kwargs[0]
    assert kw["private_key_file"] == "/keys/rsa_key.p8"
    assert "password" not in kw


def test_key_pair_passphrase_is_passed_when_set(fake_snowflake, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", "/keys/rsa_key.p8")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "topsecret")
    _connector()
    kw = fake_snowflake.connect_kwargs[0]
    assert kw["private_key_file_pwd"] == "topsecret"


def test_missing_sdk_raises_install_hint(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "snowflake", None)  # simulate not installed
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
    from filedge.connectors.snowflake import SnowflakeConnector

    with pytest.raises(ImportError, match=r"filedge\[snowflake\]"):
        SnowflakeConnector(
            account="a", user="u", warehouse="w", database="d", schema="public"
        )


def test_role_is_passed_to_connect(fake_snowflake):
    _connector(role="LOADER_ROLE")
    assert fake_snowflake.connect_kwargs[0]["role"] == "LOADER_ROLE"


def test_close_closes_the_connection(fake_snowflake):
    _connector().close()
    assert fake_snowflake.closed is True


# --- ensure_table --------------------------------------------------------------

def test_ensure_table_creates_with_provenance_and_quoted_identifiers(fake_snowflake):
    fake_snowflake.existing_columns = []  # table does not exist
    _connector().ensure_table(_config())

    create = next(s for s in fake_snowflake.statements if s.startswith("CREATE TABLE"))
    assert '"orders"' in create
    assert '"_id" NUMBER AUTOINCREMENT' in create
    assert '"name" STRING' in create
    assert '"amount" FLOAT' in create
    assert '"_source_file_hash" STRING NOT NULL' in create
    assert '"_ingested_at" TIMESTAMP_NTZ NOT NULL' in create


def test_ensure_table_matching_schema_is_ok(fake_snowflake):
    fake_snowflake.existing_columns = [
        ("_id", "NUMBER"),
        ("name", "TEXT"),
        ("amount", "FLOAT"),
        ("_source_file_hash", "TEXT"),
        ("_ingested_at", "TIMESTAMP_NTZ"),
    ]
    # Should not raise and should not issue a CREATE TABLE.
    _connector().ensure_table(_config())
    assert not any(s.startswith("CREATE TABLE") for s in fake_snowflake.statements)


def test_ensure_table_schema_mismatch_raises(fake_snowflake):
    fake_snowflake.existing_columns = [
        ("_id", "NUMBER"),
        ("name", "NUMBER"),  # declared string -> TEXT, but table has NUMBER
        ("amount", "FLOAT"),
        ("_source_file_hash", "TEXT"),
        ("_ingested_at", "TIMESTAMP_NTZ"),
    ]
    with pytest.raises(SchemaError, match="name"):
        _connector().ensure_table(_config())


# --- write_rows ----------------------------------------------------------------

def test_write_rows_deletes_then_inserts_with_provenance(fake_snowflake):
    rows = [{"name": "Alice", "amount": 1.0}, {"name": "Bob", "amount": 2.0}]
    _connector().write_rows("orders", iter(rows), "hash-1")

    # Append mode deletes the prior load for this hash before inserting.
    assert any(
        s.startswith("DELETE FROM \"orders\" WHERE \"_source_file_hash\"")
        for s in fake_snowflake.statements
    )
    assert fake_snowflake.committed == 1

    # The INSERT targets quoted columns including provenance, and each row carries
    # the file hash and an ingested-at value.
    insert_sql, batch = fake_snowflake.executemany_calls[0]
    assert '"name", "amount", "_source_file_hash", "_ingested_at"' in insert_sql
    assert batch[0][:2] == ["Alice", 1.0]
    assert batch[0][2] == "hash-1"
    assert batch[0][3]  # ingested_at present


def test_write_rows_truncate_mode(fake_snowflake):
    _connector(write_mode="truncate").write_rows(
        "orders", iter([{"name": "A", "amount": 1.0}]), "h"
    )
    assert any(s == 'TRUNCATE TABLE "orders"' for s in fake_snowflake.statements)
    assert not any(s.startswith("DELETE FROM") for s in fake_snowflake.statements)


def test_write_rows_rolls_back_on_error(fake_snowflake):
    fake_snowflake.fail_executemany = True
    with pytest.raises(RuntimeError, match="insert failed"):
        _connector().write_rows("orders", iter([{"name": "A", "amount": 1.0}]), "h")
    assert fake_snowflake.rolled_back == 1
    assert fake_snowflake.committed == 0


# --- write_cdc_rows ------------------------------------------------------------

def test_write_cdc_rows_deletes_and_inserts_then_commits(fake_snowflake):
    cdc = CdcConfig(
        keys=["id"],
        operation_column="op",
        sequence_by="seq",
        operations={"insert": ["c"], "update": ["u"], "delete": ["d"]},
    )
    rows = [
        {"id": "1", "name": "Alice", "seq": "1", "op": "c"},
        {"id": "2", "name": "Bob", "seq": "1", "op": "d"},
    ]
    _connector().write_cdc_rows("orders", iter(rows), "hash-cdc", cdc)

    assert any(s.startswith('DELETE FROM "orders" WHERE') for s in fake_snowflake.statements)
    assert any(s.startswith('INSERT INTO "orders"') for s in fake_snowflake.statements)
    assert fake_snowflake.committed == 1


def test_write_cdc_rows_rolls_back_on_error(fake_snowflake):
    fake_snowflake.fail_execute_on = "INSERT INTO"
    cdc = CdcConfig(
        keys=["id"],
        operation_column="op",
        sequence_by="seq",
        operations={"insert": ["c"], "delete": ["d"]},
    )
    rows = [{"id": "1", "name": "Alice", "seq": "1", "op": "c"}]
    with pytest.raises(RuntimeError, match="execute failed"):
        _connector().write_cdc_rows("orders", iter(rows), "h", cdc)
    assert fake_snowflake.rolled_back == 1
    assert fake_snowflake.committed == 0


# --- healthcheck ---------------------------------------------------------------

def test_healthcheck_runs_select_1(fake_snowflake):
    _connector().healthcheck()
    assert any(s.strip().upper().startswith("SELECT 1") for s in fake_snowflake.statements)


# --- registry ------------------------------------------------------------------

def test_snowflake_is_registered_with_descriptor():
    from filedge.connectors import available_connector_types, connector_descriptor

    assert "snowflake" in available_connector_types()
    desc = connector_descriptor("snowflake")
    setting_names = {s.name for s in desc.settings}
    assert {"account", "user", "warehouse", "database", "schema"} <= setting_names
    assert any(c.env_var == "SNOWFLAKE_PASSWORD" for c in desc.credential_placeholders)
