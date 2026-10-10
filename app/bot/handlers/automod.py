from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.modules.moderation import service as svc
from app.repositories import tenants as tenants_repo
from app.services import plans
from app.services.cache import Cache
from app.services.permissions import resolve_role
from app.services.roles import Role
from app.utils.time import utcnow

log = logging.getLogger(__name__)
router = Router(name="automod")

_LINK = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/)", re.I)
_INVITE = re.compile(r"(t\.me/\+|t\.me/joinchat|telegram\.me/joinchat|telegram\.dog/joinchat)", re.I)


def has_link(message: Message) -> bool:
    for ent in (message.entities or []) + (message.caption_entities or []):
        if ent.type in ("url", "text_link"):
            return True
    return bool(_LINK.search(message.text or message.caption or ""))


def has_invite(message: Message) -> bool:
    return bool(_INVITE.search(message.text or message.caption or ""))


def has_media(message: Message) -> bool:
    return any((message.photo, message.video, message.document, message.sticker, message.animation,
                message.audio, message.voice, message.video_note))


def matches_filter(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low) for w in words)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def automod(
    message: Message, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings
) -> object:
    if message.from_user is None or message.sender_chat is not None or message.from_user.is_bot:
        return UNHANDLED
    tenant = await tenants_repo.get_by_chat_id(session, message.chat.id)
    if tenant is None or not tenant.is_active:
        return UNHANDLED
    cfg = svc.mod_cfg(tenant)
    am = cfg["automod"]
    if not (any(am[k] for k in svc.FLAG_FEATURE) or cfg["slowmode"] or cfg["filters"]):
        return UNHANDLED  # nothing enabled: no further work for this message

    uid = message.from_user.id
    role = await resolve_role(session, bot, cache, settings, uid, tenant)
    if role >= Role.TRUSTED:
        return UNHANDLED
    plan = await plans.effective_view(session, cache, tenant)
    basic, advanced = plan.has("automod_basic"), plan.has("automod_advanced")
    text = message.text or message.caption or ""

    reason: str | None = None
    mute_for: timedelta | None = None
    warn = False
    if cfg["filters"] and text and matches_filter(text, cfg["filters"]):
        reason, warn = "that word is not allowed here", cfg["filter_action"] == "warn"
    elif am["antilink"] and basic and has_link(message):
        reason = "links are not allowed here"
    elif am["antiinvite"] and advanced and has_invite(message):
        reason = "invite links are not allowed here"
    elif am["antiforward"] and advanced and message.forward_origin is not None:
        reason = "forwarded messages are not allowed here"
    elif am["antimedia"] and advanced and has_media(message):
        reason = "media is not allowed here"
    elif am["antiflood"] and basic:
        n = await cache.hit(f"flood:{tenant.id}:{uid}", 10)
        if n > int(am["flood_limit"]):
            reason = "slow down"
            if n == int(am["flood_limit"]) + 1:
                mute_for = timedelta(minutes=10)
    if reason is None and am["antispam"] and advanced and text:
        digest = hashlib.md5(text.lower().encode(), usedforsecurity=False).hexdigest()[:12]
        n = await cache.hit(f"spam:{tenant.id}:{uid}:{digest}", 30)
        if n >= 3:
            reason = "please don't repeat yourself"
            if n == 3:
                mute_for = timedelta(minutes=10)
    if reason is None and cfg["slowmode"] and advanced:
        if not await cache.set_nx(f"slow:{tenant.id}:{uid}", "1", int(cfg["slowmode"])):
            reason = f"slow mode is on ({cfg['slowmode']}s)"

    if reason is None:
        return UNHANDLED

    try:
        await message.delete()
        if mute_for is not None:
            await svc.do_mute(bot, message.chat.id, uid, utcnow() + mute_for)
            await svc.log_action(session, tenant, "automute", uid, None, reason, utcnow() + mute_for)
        if warn:
            count, action = await svc.apply_warn(session, bot, tenant, uid, None, "word filter")
            reason += f" (warning {count}{', ' + action if action else ''})"
        if await cache.set_nx(f"amnote:{tenant.id}:{uid}", "1", 30):
            await bot.send_message(message.chat.id, f"⚠️ {escape(message.from_user.first_name)}, {escape(reason)}.")
    except TelegramAPIError as exc:
        log.info("automod could not act in %s: %s", message.chat.id, exc)
    return None
