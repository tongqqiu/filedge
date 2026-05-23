import pytest

from filedge.db import Database, create_audit_tables


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path}/test.db")
    create_audit_tables(database)
    return database
