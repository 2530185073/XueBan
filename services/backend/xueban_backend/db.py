import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


def database_failure_code(error):
    """Safe diagnostics only: never render a SQL statement, parameters or key."""
    if not isinstance(error, SQLAlchemyError):
        return None
    original = getattr(error, "orig", None)
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_code, int):
        if sqlite_code & 0xff == sqlite3.SQLITE_BUSY:
            return "database_busy"
        if sqlite_code & 0xff == sqlite3.SQLITE_LOCKED:
            return "database_locked"
    if getattr(original, "sqlstate", None) in {"40001", "40P01", "55P03"}:
        return "database_contention"
    return "database_integrity_error" if isinstance(error, IntegrityError) else "database_error"


def create_database_engine(database_url: str, *, allow_sqlite: bool = False):
    dialect = make_url(database_url).get_backend_name()
    if dialect == "sqlite":
        if not allow_sqlite:
            raise RuntimeError("SQLite is only an explicitly enabled local test substitute")
        # Python 3.14 sqlite3: use real transaction control, including SAVEPOINTs.
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "autocommit": False, "timeout": 10},
            pool_pre_ping=True,
        )

        @event.listens_for(engine, "connect")
        def configure_local_sqlite(connection, _):
            previous = connection.autocommit
            connection.autocommit = True
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=10000")
                # API polling must not hold a shared rollback-journal lock
                # that prevents a background task from committing. Change the
                # persistent journal only when needed; keep full durability.
                if make_url(database_url).database not in (None, "", ":memory:"):
                    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                    if mode.lower() != "wal":
                        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if mode.lower() != "wal":
                        raise RuntimeError("Local SQLite WAL mode is unavailable")
                connection.execute("PRAGMA synchronous=FULL")
            finally:
                connection.autocommit = previous

        return engine
    if dialect != "postgresql":
        raise RuntimeError("Production database must be PostgreSQL")
    return create_engine(database_url, pool_pre_ping=True)
