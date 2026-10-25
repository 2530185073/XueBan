"""Local API readers must not prevent the worker from saving a result."""
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from xueban_backend.db import create_database_engine, database_failure_code


def test_local_file_uses_wal_and_writer_commits_while_reader_stays_open(tmp_path):
    url = f"sqlite:///{tmp_path / 'isolated.db'}"
    api = create_database_engine(url, allow_sqlite=True)
    worker = create_database_engine(url, allow_sqlite=True)
    try:
        with api.begin() as connection:
            connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO sample VALUES (1)"))
        with Session(api) as reader:
            assert reader.scalar(text("SELECT count(*) FROM sample")) == 1
            with worker.begin() as writer:
                assert writer.scalar(text("PRAGMA journal_mode")) == "wal"
                assert writer.scalar(text("PRAGMA busy_timeout")) == 10000
                assert writer.scalar(text("PRAGMA foreign_keys")) == 1
                assert writer.scalar(text("PRAGMA synchronous")) == 2
                writer.execute(text("INSERT INTO sample VALUES (2)"))
            # A stable read snapshot is preserved, but it did not block commit.
            assert reader.scalar(text("SELECT count(*) FROM sample")) == 1
            reader.rollback()
            assert reader.scalar(text("SELECT count(*) FROM sample")) == 2
    finally:
        api.dispose()
        worker.dispose()


def test_memory_sqlite_preserves_transactions_and_foreign_keys():
    engine = create_database_engine("sqlite:///:memory:", allow_sqlite=True)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA journal_mode")) == "memory"
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY)"))
        with engine.connect() as connection:
            connection.execute(text("INSERT INTO sample VALUES (1)"))
            connection.rollback()
            assert connection.scalar(text("SELECT count(*) FROM sample")) == 0
    finally:
        engine.dispose()


def test_sqlite_still_requires_explicit_local_test_opt_in():
    with pytest.raises(RuntimeError, match="local test substitute"):
        create_database_engine("sqlite:///:memory:")


@pytest.mark.parametrize("number,expected", [
    (sqlite3.SQLITE_BUSY, "database_busy"),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, "database_busy"),
    (sqlite3.SQLITE_LOCKED, "database_locked"),
    (sqlite3.SQLITE_IOERR, "database_error"),
])
def test_error_diagnostics_use_fixed_codes_not_sql_or_messages(number, expected):
    original = sqlite3.OperationalError("synthetic-student-and-key-data")
    original.sqlite_errorcode = number
    failure = OperationalError("synthetic-sql", {"key": "synthetic-secret"}, original)
    assert database_failure_code(failure) == expected
    assert database_failure_code(IntegrityError("private", {}, Exception("private"))) == "database_integrity_error"
    assert database_failure_code(ValueError("private")) is None
