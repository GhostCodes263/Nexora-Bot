from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import Tenant
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services.cache import Cache
from app.services.roles import Role

log = logging.getLogger(__name__)

ANONYMOUS_ADMIN_ID = 1087968824  # Telegram's "GroupAnonymousBot"


async def telegram_role(bot: Bot, cache: Cache, chat_id: int, user_id: int) -> Role:
    """Role derived live from Telegram (never from anything the user claims). Cached 60s."""
    key = f"tgrole:{chat_id}:{user_id}"
    cached = await cache.get(key)
    if cached is not None:
        return Role(int(cached))
    role = Role.USER
    ttl = 60
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.CREATOR:
            role = Role.GROUP_OWNER
        elif member.status == ChatMemberStatus.ADMINISTRATOR:
            role = Role.ADMIN
    except TelegramAPIError as exc:
        log.warning("get_chat_member failed: %s", exc)
        ttl = 10
    await cache.set(key, str(int(role)), ttl)
    return role


async def resolve_role(
    session: AsyncSession,
    bot: Bot,
    cache: Cache,
    settings: Settings,
    user_id: int,
    tenant: Tenant | None,
    *,
    anonymous_admin: bool = False,
) -> Role:
    if settings.owner_id and user_id == settings.owner_id:
        return Role.OWNER
    global_role = await users_repo.get_global_role(session, user_id)
    best = Role(global_role) if global_role is not None else Role.USER
    if tenant is not None:
        stored = await tenants_repo.get_role(session, tenant.id, user_id)
        if stored is not None:
            best = max(best, Role(stored))
        if anonymous_admin:
            best = max(best, Role.ADMIN)
        else:
            best = max(best, await telegram_role(bot, cache, tenant.chat_id, user_id))
    return best


def can_manage(actor: Role, target_current: Role, new_role: Role | None = None) -> bool:
    """An actor may only change people strictly below them, and only grant roles strictly below."""
    if actor <= target_current:
        return False
    return new_role is None or actor > new_role
