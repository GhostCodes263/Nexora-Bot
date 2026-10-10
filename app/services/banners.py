from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Tenant
from app.repositories import tenants as tenants_repo
from app.services import plans
from app.services.cache import Cache

log = logging.getLogger(__name__)

# Every screen that can carry a banner image (also the file names in assets/banners/).
KEYS = ["menu", "welcome", "help", "economy", "games", "entertainment", "dating", "moderation", "management",
        "utility", "rentals", "vip", "verification", "payments", "settings", "owner", "admin"]
CAPTION_LIMIT = 1000  # Telegram captions are limited to 1024 characters


async def global_banners(session: AsyncSession, cache: Cache) -> dict:
    cached = await cache.get("banners:global")
    if cached is not None:
        return json.loads(cached)
    data = await tenants_repo.get_global(session, "banners")
    await cache.set("banners:global", json.dumps(data), 60)
    return data


async def resolve(session: AsyncSession, cache: Cache, tenant: Tenant | None, key: str) -> str | None:
    """Pick the banner file_id for a screen. A group's own banners (Pro plan and up) win over the bot's."""
    if tenant is not None:
        own = (tenant.settings or {}).get("banners", {})
        if own and (own.get(key) or own.get("menu")):
            view = await plans.effective_view(session, cache, tenant)
            if view.has("branding"):
                return own.get(key) or own.get("menu")
    g = await global_banners(session, cache)
    return g.get(key) or g.get("menu")


async def set_global(session: AsyncSession, cache: Cache, key: str, file_id: str) -> None:
    data = await tenants_repo.get_global(session, "banners")
    await tenants_repo.set_global(session, "banners", {**data, key: file_id})
    await cache.delete("banners:global")


async def delete_global(session: AsyncSession, cache: Cache, key: str) -> bool:
    data = await tenants_repo.get_global(session, "banners")
    if key not in data:
        return False
    await tenants_repo.set_global(session, "banners", {k: v for k, v in data.items() if k != key})
    await cache.delete("banners:global")
    return True


def set_tenant(tenant: Tenant, key: str, file_id: str | None) -> bool:
    own = dict((tenant.settings or {}).get("banners", {}))
    if file_id is None:
        if key not in own:
            return False
        own.pop(key)
    else:
        own[key] = file_id
    tenant.settings = {**(tenant.settings or {}), "banners": own}  # reassign so the change is saved
    return True
