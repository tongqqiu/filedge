import pytest

from filedge.connectors import Connector
from filedge.connectors.bigquery import BigQueryConnector
from filedge.connectors.databricks import DatabricksConnector
from filedge.connectors.duckdb import DuckDBConnector
from filedge.connectors.postgres import PostgresConnector


class DummyConnector(Connector):
    def ensure_table(self, config):
        pass

    def write_rows(self, table, rows, file_hash):
        pass


def test_base_connector_healthcheck_requires_override():
    with pytest.raises(NotImplementedError, match="does not support healthcheck"):
        DummyConnector().healthcheck()


def test_base_connector_close_is_noop():
    DummyConnector().close()


def test_bigquery_healthcheck_runs_read_only_query():
    class FakeJob:
        def __init__(self):
            self.result_called = False

        def result(self):
            self.result_called = True
            return [1]

    class FakeClient:
        def __init__(self):
            self.sql = None
            self.job = FakeJob()
            self.closed = False

        def query(self, sql):
            self.sql = sql
            return self.job

        def close(self):
            self.closed = True

    connector = BigQueryConnector.__new__(BigQueryConnector)
    connector._bq = FakeClient()

    connector.healthcheck()
    connector.close()

    assert connector._bq.sql == "SELECT 1"
    assert connector._bq.job.result_called is True
    assert connector._bq.closed is True


def test_databricks_healthcheck_runs_select_one():
    connection = _CursorConnection(fetch_method="fetchall")
    connector = DatabricksConnector.__new__(DatabricksConnector)
    connector._conn = connection

    connector.healthcheck()
    connector.close()

    assert connection.cursor_obj.executed == "SELECT 1"
    assert connection.cursor_obj.fetchall_called is True
    assert connection.closed is True


def test_postgres_healthcheck_runs_select_one():
    connection = _CursorConnection(fetch_method="fetchone")
    connector = PostgresConnector.__new__(PostgresConnector)
    connector._conn = connection

    connector.healthcheck()
    connector.close()

    assert connection.cursor_obj.executed == "SELECT 1"
    assert connection.cursor_obj.fetchone_called is True
    assert connection.closed is True


def test_duckdb_healthcheck_runs_select_one():
    class FakeResult:
        def __init__(self):
            self.fetchone_called = False

        def fetchone(self):
            self.fetchone_called = True
            return (1,)

    class FakeConnection:
        def __init__(self):
            self.sql = None
            self.result = FakeResult()
            self.closed = False

        def execute(self, sql):
            self.sql = sql
            return self.result

        def close(self):
            self.closed = True

    connector = DuckDBConnector.__new__(DuckDBConnector)
    connector._conn = FakeConnection()

    connector.healthcheck()
    connector.close()

    assert connector._conn.sql == "SELECT 1"
    assert connector._conn.result.fetchone_called is True
    assert connector._conn.closed is True


class _CursorConnection:
    def __init__(self, fetch_method):
        self.cursor_obj = _Cursor(fetch_method)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class _Cursor:
    def __init__(self, fetch_method):
        self.fetch_method = fetch_method
        self.executed = None
        self.fetchall_called = False
        self.fetchone_called = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql):
        self.executed = sql

    def fetchall(self):
        if self.fetch_method != "fetchall":
            raise AssertionError("fetchall should not be called")
        self.fetchall_called = True
        return [(1,)]

    def fetchone(self):
        if self.fetch_method != "fetchone":
            raise AssertionError("fetchone should not be called")
        self.fetchone_called = True
        return (1,)
