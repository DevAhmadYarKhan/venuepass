"""Run migrations using the same settings and model metadata as the API."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base
from app import models  # Register mapped tables before Alembic inspects metadata.

# Import future model modules here as well so autogeneration sees their tables.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migration SQL without opening a database connection (--sql)."""
    context.configure(
        url=str(get_settings().database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Run Alembic's synchronous migration API on the supplied connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Apply migrations through an async engine with deterministic cleanup."""
    # CLI migrations are short-lived, so there is no need to retain a pool.
    engine = create_async_engine(
        str(get_settings().database_url), poolclass=pool.NullPool
    )
    try:
        async with engine.connect() as connection:
            # Bridge Alembic's synchronous API to SQLAlchemy's async connection.
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


# Alembic's --sql flag selects rendering; normal commands connect to PostgreSQL.
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
