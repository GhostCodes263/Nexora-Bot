from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import GlobalRole, User
from app.utils.time import aware, utcnow


async def upsert_user(session: AsyncSession, user_id: int, username: str | None, first_name: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, username=username, first_name=first_name)
        try:
            async with session.begin_nested():
                session.add(user)
                await session.flush()
        except IntegrityError:
            user = await session.get(User, user_id)
            assert user is not None
        return user
    now = utcnow()
    last = aware(user.last_seen_at) or now
    if user.username != username or user.first_name != first_name or now - last > timedelta(minutes=5):
        user.username = username
        user.first_name = first_name
        user.last_seen_at = now
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def find_by_username(session: AsyncSession, username: str) -> User | None:
    res = await session.execute(select(User).where(func.lower(User.username) == username.lower().lstrip("@")))
    return res.scalars().first()


async def count_users(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(User))).scalar_one())


async def set_global_ban(session: AsyncSession, user_id: int, banned: bool) -> User | None:
    user = await session.get(User, user_id)
    if user is not None:
        user.is_globally_banned = banned
    return user


async def get_global_role(session: AsyncSession, user_id: int) -> int | None:
    row = await session.get(GlobalRole, user_id)
    return row.role if row else None


async def set_global_role(session: AsyncSession, user_id: int, role: int, granted_by: int) -> None:
    row = await session.get(GlobalRole, user_id)
    if row is None:
        session.add(GlobalRole(user_id=user_id, role=role, granted_by=granted_by))
    else:
        row.role = role
        row.granted_by = granted_by


async def remove_global_role(session: AsyncSession, user_id: int) -> bool:
    row = await session.get(GlobalRole, user_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def recent_users(session: AsyncSession, limit: int = 10) -> list[User]:
    res = await session.execute(select(User).order_by(User.last_seen_at.desc()).limit(limit))
    return list(res.scalars())
