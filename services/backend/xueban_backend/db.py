from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url


def create_database_engine(database_url: str, *, allow_sqlite: bool = False):
    dialect = make_url(database_url).get_backend_name()
    if dialect == "sqlite":
        if not allow_sqlite:
            raise RuntimeError("SQLite is only an explicitly enabled local test substitute")
        # Python 3.14 sqlite3: use real transaction control, including SAVEPOINTs.
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "autocommit": False},
            pool_pre_ping=True,
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            previous = connection.autocommit
            connection.autocommit = True
            connection.execute("PRAGMA foreign_keys=ON")
            connection.autocommit = previous

        return engine
    if dialect != "postgresql":
        raise RuntimeError("Production database must be PostgreSQL")
    return create_engine(database_url, pool_pre_ping=True)
