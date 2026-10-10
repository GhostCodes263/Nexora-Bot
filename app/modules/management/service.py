from __future__ import annotations

import json
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Tenant, Trigger
from app.services.cache import Cache

DEFAULT_MGMT: dict = {
    "welcome": {"on": False, "text": "👋 Welcome {mention} to {title}!"},
    "goodbye": {"on": False, "text": "👋 {name} left the group."},
    "rules": "",
    "captcha": {"on": False, "minutes": 5},
}


def mg_cfg(tenant: Tenant) -> dict:
    stored = (tenant.settings or {}).get("mgmt", {})
    return {
        "welcome": {**DEFAULT_MGMT["welcome"], **stored.get("welcome", {})},
        "goodbye": {**DEFAULT_MGMT["goodbye"], **stored.get("goodbye", {})},
        "rules": stored.get("rules", ""),
        "captcha": {**DEFAULT_MGMT["captcha"], **stored.get("captcha", {})},
    }


def save_mg_cfg(tenant: Tenant, cfg: dict) -> None:
    tenant.settings = {**(tenant.settings or {}), "mgmt": cfg}


def render(template: str, user_id: int, full_name: str, title: str, count: int | str = "") -> str:
    """Fill {name} {mention} {title} {count}. User-controlled values are escaped; the template is not."""
    mention = f'<a href="tg://user?id={user_id}">{escape(full_name)}</a>'
    return (template.replace("{mention}", mention).replace("{name}", escape(full_name))
            .replace("{title}", escape(title)).replace("{count}", str(count)))


async def get_triggers(session: AsyncSession, cache: Cache, tenant_id: int) -> list[tuple[str, str]]:
    key = f"trg:{tenant_id}"
    cached = await cache.get(key)
    if cached is not None:
        return [tuple(x) for x in json.loads(cached)]  # type: ignore[misc]
    rows = (await session.execute(select(Trigger).where(Trigger.tenant_id == tenant_id))).scalars().all()
    data = [(r.keyword, r.response) for r in rows]
    await cache.set(key, json.dumps(data), 60)
    return data


async def invalidate_triggers(cache: Cache, tenant_id: int) -> None:
    await cache.delete(f"trg:{tenant_id}")
