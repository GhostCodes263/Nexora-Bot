from __future__ import annotations

import logging
import secrets

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import tenants as tenants_repo
from app.services import economy as eco
from app.services import tenants as tenant_service
from app.services.cache import Cache

log = logging.getLogger(__name__)
router = Router(name="xp")
GROUPS = {"group", "supergroup"}


@router.message(F.text, F.chat.type.in_(GROUPS))
async def chat_xp(message: Message, session: AsyncSession, cache: Cache) -> object:
    """Members earn a little XP for chatting (at most once a minute each). Never consumes the message."""
    user, text = message.from_user, message.text or ""
    if user is None or user.is_bot or message.sender_chat is not None or text.startswith("/") or len(text) < 3:
        return UNHANDLED
    # Cheap in-memory/Redis check first: most messages stop here and never touch the database.
    if not await cache.set_nx(f"xp:{message.chat.id}:{user.id}", "1", 60):
        return UNHANDLED
    tenant = await tenants_repo.get_by_chat_id(session, message.chat.id)
    if tenant is None or not tenant.is_active or not tenant_service.module_enabled(tenant, "economy"):
        return UNHANDLED
    cfg = eco.eco_cfg(tenant)
    lo, hi = int(cfg["xp_min"]), int(cfg["xp_max"])
    gain = lo + secrets.randbelow(max(hi - lo, 0) + 1)
    if gain <= 0:
        return UNHANDLED
    acct = await eco.get_account(session, tenant, user.id, lock=True)
    new_level = eco.add_xp(acct, gain)
    if new_level:
        eco.change_wallet(session, acct, new_level * 50, "level_up")
        try:
            await message.reply(f"🎉 {user.first_name} reached <b>level {new_level}</b>! Bonus {eco.fmt(cfg, new_level * 50)}")
        except TelegramAPIError:
            pass
    return UNHANDLED
