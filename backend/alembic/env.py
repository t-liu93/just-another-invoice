"""Alembic environment.

Wired to:
  - import ``Base.metadata`` from the app's models (autogenerate target)
  - pull the database URL from ``jai.config.Settings`` rather than
    from ``alembic.ini`` (single source of truth)
  - support async migration execution (PostgreSQL + asyncpg)
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Importing the models package populates Base.metadata with every table.
from jai import models  # noqa: F401  (side-effect import)
from jai.config import get_settings
from jai.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override alembic.ini's empty sqlalchemy.url with the app's configured URL,
# unless the caller passed -x url=... on the CLI (lets tests point elsewhere).
_x_args = context.get_x_argument(as_dictionary=True)
_url = _x_args.get("url") or get_settings().database_url
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Apply migrations on a live connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine, run migrations, and dispose."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online (live DB) migration."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
