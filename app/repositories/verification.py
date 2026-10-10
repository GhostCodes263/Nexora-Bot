from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    VerificationAction,
    VerificationApplication,
    VerificationReviewer,
)
from app.repositories import tenants as tenants_repo
from app.services.roles import Role
from app.utils.time import utcnow

STATUSES = ("PENDING", "UNDER_REVIEW", "APPROVED", "REJECTED", "NEEDS_MORE_INFORMATION", "SUSPENDED")
OPEN_STATUSES = ("PENDING", "UNDER_REVIEW", "NEEDS_MORE_INFORMATION")


async def latest(session: AsyncSession, user_id: int) -> VerificationApplication | None:
    res = await session.execute(
        select(VerificationApplication).where(VerificationApplication.user_id == user_id)
        .order_by(VerificationApplication.id.desc()).limit(1)
    )
    return res.scalars().first()


async def is_approved(session: AsyncSession, user_id: int) -> bool:
    app = await latest(session, user_id)
    return app is not None and app.status == "APPROVED"


async def get_by_public_id(session: AsyncSession, public_id: str) -> VerificationApplication | None:
    pid = public_id.strip().upper()
    if not pid.startswith("V-"):
        pid = "V-" + pid
    res = await session.execute(select(VerificationApplication).where(VerificationApplication.public_id == pid))
    return res.scalars().first()


async def create(
    session: AsyncSession, user_id: int, answers: dict, photo_file_id: str | None
) -> VerificationApplication:
    for _ in range(20):
        public_id = f"V-{secrets.randbelow(90000) + 10000}"
        app = VerificationApplication(
            public_id=public_id, user_id=user_id, status="PENDING", answers=answers, photo_file_id=photo_file_id,
        )
        try:
            async with session.begin_nested():
                session.add(app)
                await session.flush()
            await add_action(session, app, user_id, "SUBMITTED")
            return app
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a verification ID")


async def add_action(session: AsyncSession, app: VerificationApplication, actor_id: int, action: str, note: str = "") -> None:
    """Every verification action is stored on the application and mirrored in the audit log."""
    session.add(VerificationAction(application_id=app.id, actor_id=actor_id, action=action, note=note[:500]))
    await tenants_repo.add_audit(session, f"verification_{action.lower()}", actor_id, None,
                                 {"application": app.public_id})


async def set_status(
    session: AsyncSession, app: VerificationApplication, status: str, actor_id: int, note: str = ""
) -> None:
    assert status in STATUSES
    app.status = status
    app.reviewer_note = note[:500] or app.reviewer_note
    app.updated_at = utcnow()
    await add_action(session, app, actor_id, status, note)


async def queue(session: AsyncSession, limit: int = 10) -> list[VerificationApplication]:
    res = await session.execute(
        select(VerificationApplication).where(VerificationApplication.status.in_(OPEN_STATUSES))
        .order_by(VerificationApplication.id).limit(limit)
    )
    return list(res.scalars())


async def is_reviewer(session: AsyncSession, user_id: int, role: Role) -> bool:
    if role >= Role.OWNER:
        return True
    return await session.get(VerificationReviewer, user_id) is not None


async def add_reviewer(session: AsyncSession, user_id: int, added_by: int) -> bool:
    if await session.get(VerificationReviewer, user_id) is not None:
        return False
    session.add(VerificationReviewer(user_id=user_id, added_by=added_by))
    return True


async def remove_reviewer(session: AsyncSession, user_id: int) -> bool:
    row = await session.get(VerificationReviewer, user_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def list_reviewers(session: AsyncSession) -> list[VerificationReviewer]:
    return list((await session.execute(select(VerificationReviewer))).scalars())
