"""
Alembic environment. Points at the same DATABASE_URL and the same
SQLAlchemy metadata (app.db.base.Base) the app itself uses, so there is
one source of truth for both "what the app thinks the schema is" and
"what the app connects to" - never a second, migration-only config that
can quietly drift from app/config.py.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# backend/ (this file's grandparent) is where the `app` package lives -
# add it to sys.path so `from app...` works regardless of the directory
# alembic was invoked from, as long as it was pointed at this alembic.ini.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATABASE_URL  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: E402,F401 - import registers every table on Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout (`alembic upgrade head --sql`) without a live
    DB connection - not used by the normal workflow in this project's
    README, kept for completeness/parity with Alembic's default template."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite can't ALTER TABLE in most of the ways a migration
            # might need (drop/modify a column, add certain constraints)
            # without recreating the table - batch mode makes Alembic do
            # that copy-and-swap automatically. A no-op on dialects that
            # don't need it (e.g. Postgres in production).
            render_as_batch=connection.engine.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
