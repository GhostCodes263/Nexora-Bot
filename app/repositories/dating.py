from __future__ import annotations

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database.models import (
    Couple,
    DatingBlock,
    DatingMatch,
    DatingPool,
    DatingProfile,
    DatingReport,
    DatingSwipe,
    Tenant,
)
from app.utils.time import utcnow


def pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


# --- profiles ----------------------------------------------------------------------------------------

async def get_profile(session: AsyncSession, user_id: int) -> DatingProfile | None:
    return await session.get(DatingProfile, user_id)


async def save_profile(session: AsyncSession, user_id: int, data: dict, photo: str | None) -> DatingProfile:
    p = await get_profile(session, user_id)
    if p is None:
        p = DatingProfile(user_id=user_id, **data, photo_file_id=photo)
        session.add(p)
    else:
        for k, v in data.items():
            setattr(p, k, v)
        if photo is not None:
            p.photo_file_id = photo
        p.updated_at = utcnow()
    return p


async def delete_all(session: AsyncSession, user_id: int) -> None:
    """Erase a person's dating data. Reports filed AGAINST them are kept (safety), without their profile."""
    await session.execute(delete(DatingPool).where(DatingPool.user_id == user_id))
    await session.execute(delete(DatingSwipe).where(or_(DatingSwipe.from_id == user_id, DatingSwipe.to_id == user_id)))
    await session.execute(delete(DatingMatch).where(or_(DatingMatch.user_a == user_id, DatingMatch.user_b == user_id)))
    await session.execute(delete(Couple).where(or_(Couple.user_a == user_id, Couple.user_b == user_id)))
    await session.execute(delete(DatingBlock).where(DatingBlock.user_id == user_id))
    await session.execute(delete(DatingReport).where(DatingReport.reporter_id == user_id))
    await session.execute(delete(DatingProfile).where(DatingProfile.user_id == user_id))


# --- pools (a pool = the members of ONE group who opted in) -----------------------------------------------

async def join_pool(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    res = await session.execute(select(DatingPool.id).where(DatingPool.tenant_id == tenant_id, DatingPool.user_id == user_id))
    if res.scalars().first() is not None:
        return False
    session.add(DatingPool(tenant_id=tenant_id, user_id=user_id))
    return True


async def leave_pool(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    res = await session.execute(delete(DatingPool).where(DatingPool.tenant_id == tenant_id, DatingPool.user_id == user_id))
    return bool(res.rowcount)


async def pools_of(session: AsyncSession, user_id: int) -> list[Tenant]:
    q = select(Tenant).join(DatingPool, DatingPool.tenant_id == Tenant.id).where(DatingPool.user_id == user_id)
    return list((await session.execute(q)).scalars())


async def in_pool(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    res = await session.execute(select(DatingPool.id).where(DatingPool.tenant_id == tenant_id, DatingPool.user_id == user_id))
    return res.scalars().first() is not None


async def shared_tenants(session: AsyncSession, a: int, b: int) -> list[int]:
    pa, pb = aliased(DatingPool), aliased(DatingPool)
    q = select(pa.tenant_id).join(pb, pb.tenant_id == pa.tenant_id).where(pa.user_id == a, pb.user_id == b)
    return list((await session.execute(q)).scalars())


# --- blocks ----------------------------------------------------------------------------------------------------

async def is_blocked(session: AsyncSession, a: int, b: int) -> bool:
    q = select(DatingBlock.id).where(or_(and_(DatingBlock.user_id == a, DatingBlock.blocked_id == b),
                                         and_(DatingBlock.user_id == b, DatingBlock.blocked_id == a))).limit(1)
    return (await session.execute(q)).scalars().first() is not None


async def add_block(session: AsyncSession, user_id: int, blocked_id: int) -> bool:
    res = await session.execute(select(DatingBlock.id).where(DatingBlock.user_id == user_id, DatingBlock.blocked_id == blocked_id))
    if res.scalars().first() is not None:
        return False
    session.add(DatingBlock(user_id=user_id, blocked_id=blocked_id))
    return True


async def remove_block(session: AsyncSession, user_id: int, blocked_id: int) -> bool:
    res = await session.execute(delete(DatingBlock).where(DatingBlock.user_id == user_id, DatingBlock.blocked_id == blocked_id))
    return bool(res.rowcount)


async def blocks_of(session: AsyncSession, user_id: int) -> list[int]:
    return list((await session.execute(select(DatingBlock.blocked_id).where(DatingBlock.user_id == user_id))).scalars())


# --- discovery & swipes --------------------------------------------------------------------------------------------

async def candidates(session: AsyncSession, me: DatingProfile, limit: int = 30) -> list[tuple[DatingProfile, int]]:
    """Random profiles from pools I'm in: not me, visible, not already swiped, not blocked either way."""
    mine, theirs = aliased(DatingPool), aliased(DatingPool)
    swiped = exists().where(DatingSwipe.tenant_id == theirs.tenant_id, DatingSwipe.from_id == me.user_id,
                            DatingSwipe.to_id == DatingProfile.user_id)
    blocked = exists().where(or_(
        and_(DatingBlock.user_id == me.user_id, DatingBlock.blocked_id == DatingProfile.user_id),
        and_(DatingBlock.user_id == DatingProfile.user_id, DatingBlock.blocked_id == me.user_id)))
    q = (select(DatingProfile, theirs.tenant_id)
         .join(theirs, theirs.user_id == DatingProfile.user_id)
         .join(mine, and_(mine.tenant_id == theirs.tenant_id, mine.user_id == me.user_id))
         .where(DatingProfile.user_id != me.user_id, DatingProfile.hidden.is_(False),
                DatingProfile.opted_out.is_(False), ~swiped, ~blocked)
         .order_by(func.random()).limit(limit))
    return [(p, t) for p, t in (await session.execute(q)).all()]


async def record_swipe(session: AsyncSession, tenant_id: int, from_id: int, to_id: int, kind: str) -> bool:
    """Store a swipe. Returns True only when this swipe creates a NEW mutual match."""
    res = await session.execute(select(DatingSwipe).where(
        DatingSwipe.tenant_id == tenant_id, DatingSwipe.from_id == from_id, DatingSwipe.to_id == to_id))
    row = res.scalars().first()
    if row is None:
        session.add(DatingSwipe(tenant_id=tenant_id, from_id=from_id, to_id=to_id, kind=kind))
    else:
        row.kind = kind
    if kind not in ("like", "crush"):
        return False
    rev = await session.execute(select(DatingSwipe.id).where(
        DatingSwipe.tenant_id == tenant_id, DatingSwipe.from_id == to_id, DatingSwipe.to_id == from_id,
        DatingSwipe.kind.in_(("like", "crush"))))
    if rev.scalars().first() is None:
        return False
    a, b = pair(from_id, to_id)
    have = await session.execute(select(DatingMatch.id).where(
        DatingMatch.tenant_id == tenant_id, DatingMatch.user_a == a, DatingMatch.user_b == b))
    if have.scalars().first() is not None:
        return False
    session.add(DatingMatch(tenant_id=tenant_id, user_a=a, user_b=b))
    return True


async def matches_of(session: AsyncSession, user_id: int) -> list[DatingMatch]:
    q = select(DatingMatch).where(or_(DatingMatch.user_a == user_id, DatingMatch.user_b == user_id)).order_by(DatingMatch.id.desc())
    return list((await session.execute(q)).scalars())


async def get_match(session: AsyncSession, tenant_id: int, a: int, b: int) -> DatingMatch | None:
    x, y = pair(a, b)
    res = await session.execute(select(DatingMatch).where(
        DatingMatch.tenant_id == tenant_id, DatingMatch.user_a == x, DatingMatch.user_b == y))
    return res.scalars().first()


async def stats(session: AsyncSession, user_id: int) -> dict:
    async def count(model, *where) -> int:
        return int((await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one())

    return {
        "likes_given": await count(DatingSwipe, DatingSwipe.from_id == user_id, DatingSwipe.kind.in_(("like", "crush"))),
        "passes": await count(DatingSwipe, DatingSwipe.from_id == user_id, DatingSwipe.kind == "dislike"),
        "likes_received": await count(DatingSwipe, DatingSwipe.to_id == user_id, DatingSwipe.kind.in_(("like", "crush"))),
        "crushes_received": await count(DatingSwipe, DatingSwipe.to_id == user_id, DatingSwipe.kind == "crush"),
        "matches": await count(DatingMatch, or_(DatingMatch.user_a == user_id, DatingMatch.user_b == user_id)),
    }


# --- reports ---------------------------------------------------------------------------------------------------------

async def add_report(session: AsyncSession, reporter: int, reported: int, tenant_id: int | None, reason: str) -> None:
    session.add(DatingReport(reporter_id=reporter, reported_id=reported, tenant_id=tenant_id, reason=reason[:200]))


# --- couples ------------------------------------------------------------------------------------------------------------

async def couple_of(session: AsyncSession, tenant_id: int, user_id: int) -> Couple | None:
    res = await session.execute(select(Couple).where(
        Couple.tenant_id == tenant_id, or_(Couple.user_a == user_id, Couple.user_b == user_id)))
    return res.scalars().first()


async def couples_of(session: AsyncSession, user_id: int) -> list[Couple]:
    res = await session.execute(select(Couple).where(or_(Couple.user_a == user_id, Couple.user_b == user_id)))
    return list(res.scalars())


async def create_couple(session: AsyncSession, tenant_id: int, a: int, b: int) -> Couple:
    x, y = pair(a, b)
    c = Couple(tenant_id=tenant_id, user_a=x, user_b=y, xp=0)
    session.add(c)
    await session.flush()
    return c
