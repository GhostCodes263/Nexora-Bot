from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import tenants as tenants_repo

BILLING_DEFAULTS: dict = {
    "trial_days": 7,
    "grace_days": 3,
    "trials_per_user": 2,
    "reminder_days": [3, 1],
}
VIP_DEFAULTS: dict = {
    "monthly_stars": 500,
    "annual_stars": 5000,
    "grace_days": 2,
    "reminder_days": [3, 1],
    "channel_id": 0,
    "require_verification": True,
}
VERIFICATION_DEFAULTS: dict = {
    "require_photo": False,
    "extra_questions": [],
}


async def load(session: AsyncSession, key: str, defaults: dict) -> dict:
    """Owner-editable configuration: defaults overlaid with whatever is stored in the database."""
    return {**defaults, **(await tenants_repo.get_global(session, key))}


async def billing(session: AsyncSession) -> dict:
    return await load(session, "billing", BILLING_DEFAULTS)


async def vip(session: AsyncSession) -> dict:
    return await load(session, "vip", VIP_DEFAULTS)


async def verification(session: AsyncSession) -> dict:
    return await load(session, "verification", VERIFICATION_DEFAULTS)
