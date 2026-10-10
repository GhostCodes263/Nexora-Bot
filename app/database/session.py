from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_engine: AsyncEngine | None = None
_maker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str) -> AsyncEngine:
    global _engine, _maker
    _engine = create_async_engine(url, pool_pre_ping=True)
    _maker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_maker() -> async_sessionmaker[AsyncSession]:
    if _maker is None:
        raise RuntimeError("Database not initialised")
    return _maker


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()
