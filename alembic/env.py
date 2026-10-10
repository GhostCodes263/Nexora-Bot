from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import get_settings
from app.database import models  # noqa: F401  (registers tables on Base.metadata)
from app.database.base import Base

config = context.config
target_metadata = Base.metadata
URL = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True, version_table="tp_alembic_version")
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        # Serialise migrations if two instances start at once (e.g. during a deploy overlap).
        connection.execute(text("SELECT pg_advisory_lock(727274)"))
        connection.commit()
    context.configure(
        connection=connection, target_metadata=target_metadata, version_table="tp_alembic_version"
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_async())
