import os

from alembic import context
from xueban_backend.config import database_url_from_environment
from xueban_backend.db import create_database_engine
from xueban_backend.models import Base

config = context.config
database_url = config.attributes.get("database_url") or database_url_from_environment()
if not database_url:
    raise RuntimeError("DATABASE_URL is required for migrations")
allow_sqlite = config.attributes.get("allow_sqlite", False) or os.environ.get("ALLOW_SQLITE_FOR_TESTS") == "1"

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_database_engine(database_url, allow_sqlite=allow_sqlite)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
