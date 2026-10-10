from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Plan, Rental, Tenant
from app.services.cache import Cache

ACTIVE_STATES = ("trial", "active", "grace")


@dataclass(frozen=True)
class PlanView:
    code: str
    name: str
    features: tuple[str, ...] = ()
    limits: dict = field(default_factory=dict)
    price_month_cents: int = 0
    price_year_cents: int = 0
    stars_month: int = 0
    stars_year: int = 0

    def has(self, feature: str) -> bool:
        return feature in self.features

    def limit(self, key: str, default: int = 0) -> int:
        return int(self.limits.get(key, default))


FALLBACK_FREE = PlanView(
    "free", "Free", ("moderation_basic",),
    {"filters": 5, "custom_commands": 0, "triggers": 0, "scheduled": 0},
)


def to_view(p: Plan) -> PlanView:
    return PlanView(
        p.code, p.name, tuple(p.features or ()), dict(p.limits or {}),
        p.price_month_cents, p.price_year_cents, p.stars_month, p.stars_year,
    )


async def get_plan(session: AsyncSession, code: str) -> Plan | None:
    return await session.get(Plan, code)


async def list_plans(session: AsyncSession, only_active: bool = True) -> list[Plan]:
    q = select(Plan).order_by(Plan.sort)
    if only_active:
        q = q.where(Plan.is_active.is_(True))
    return list((await session.execute(q)).scalars())


async def get_rental(session: AsyncSession, tenant_id: int) -> Rental | None:
    res = await session.execute(select(Rental).where(Rental.tenant_id == tenant_id))
    return res.scalars().first()


async def invalidate(cache: Cache, tenant_id: int) -> None:
    await cache.delete(f"plan:{tenant_id}")


async def effective_view(session: AsyncSession, cache: Cache, tenant: Tenant) -> PlanView:
    """The plan a tenant is entitled to *right now* (expired rentals fall back to Free)."""
    key = f"plan:{tenant.id}"
    cached = await cache.get(key)
    if cached:
        d = json.loads(cached)
        d["features"] = tuple(d["features"])
        return PlanView(**d)
    rental = await get_rental(session, tenant.id)
    plan = None
    if rental is not None and rental.status in ACTIVE_STATES:
        plan = await get_plan(session, rental.plan_code)
    if plan is None:
        plan = await get_plan(session, "free")
    view = to_view(plan) if plan is not None else FALLBACK_FREE
    await cache.set(key, json.dumps(asdict(view)), 60)
    return view


async def min_plan_name(session: AsyncSession, feature: str) -> str:
    for p in await list_plans(session):
        if feature in (p.features or []):
            return p.name
    return "a paid plan"


def money(cents: int) -> str:
    return f"${cents / 100:.2f}"


FEATURE_LABELS = {
    "moderation_basic": "Basic moderation",
    "welcome": "Welcome & goodbye messages, rules",
    "captcha": "Join captcha",
    "automod_basic": "Anti-link & anti-flood",
    "auto_replies": "Keyword auto-replies",
    "custom_commands": "Custom commands",
    "scheduled_messages": "Scheduled messages",
    "automod_advanced": "Advanced auto-mod (anti-invite/forward/bot/media, anti-spam, slow mode)",
}
LIMIT_LABELS = {
    "filters": "word filters",
    "custom_commands": "custom commands",
    "triggers": "auto-replies",
    "scheduled": "scheduled messages",
}
