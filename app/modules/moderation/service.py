from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatPermissions
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ModAction, ModWarning, Tenant
from app.repositories import tenants as tenants_repo
from app.utils.time import utcnow

log = logging.getLogger(__name__)

FLAG_FEATURE = {
    "antilink": "automod_basic",
    "antiflood": "automod_basic",
    "antiinvite": "automod_advanced",
    "antiforward": "automod_advanced",
    "antibot": "automod_advanced",
    "antimedia": "automod_advanced",
    "antispam": "automod_advanced",
}
DEFAULT_MOD: dict = {
    "warn_actions": {"3": "mute", "5": "kick", "7": "ban"},
    "warn_mute_hours": 24,
    "automod": {k: False for k in FLAG_FEATURE} | {"flood_limit": 6},
    "slowmode": 0,
    "filters": [],
    "filter_action": "delete",  # delete | warn
}

MUTED = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False, can_send_photos=False,
    can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False,
)
FULL = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
    can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
)
LOCK_FIELDS = {
    "messages": ["can_send_messages"],
    "media": ["can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos",
              "can_send_video_notes", "can_send_voice_notes"],
    "stickers": ["can_send_other_messages"],
    "polls": ["can_send_polls"],
    "previews": ["can_add_web_page_previews"],
}

_DUR = re.compile(r"^(\d+)([smhdw])$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> timedelta | None:
    """'30m', '2h', '7d', '1w' -> timedelta. Telegram treats <30s or >366d as permanent, so bound it."""
    m = _DUR.match(text.strip().lower())
    if not m:
        return None
    delta = timedelta(seconds=int(m.group(1)) * _UNIT[m.group(2)])
    if delta < timedelta(minutes=1) or delta > timedelta(days=366):
        return None
    return delta


def fmt_duration(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size and secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def mod_cfg(tenant: Tenant) -> dict:
    stored = (tenant.settings or {}).get("mod", {})
    cfg = {**DEFAULT_MOD, **stored}
    cfg["automod"] = {**DEFAULT_MOD["automod"], **stored.get("automod", {})}
    cfg["warn_actions"] = dict(stored.get("warn_actions", DEFAULT_MOD["warn_actions"]))
    cfg["filters"] = list(stored.get("filters", []))
    return cfg


def save_mod_cfg(tenant: Tenant, cfg: dict) -> None:
    tenant.settings = {**(tenant.settings or {}), "mod": cfg}  # reassign so the JSON change is saved


# --- Telegram actions ---------------------------------------------------------------------------

async def do_ban(bot: Bot, chat_id: int, uid: int, until: datetime | None = None) -> None:
    await bot.ban_chat_member(chat_id, uid, until_date=until)


async def do_unban(bot: Bot, chat_id: int, uid: int) -> None:
    await bot.unban_chat_member(chat_id, uid, only_if_banned=True)


async def do_kick(bot: Bot, chat_id: int, uid: int) -> None:
    await bot.ban_chat_member(chat_id, uid)
    await bot.unban_chat_member(chat_id, uid, only_if_banned=True)


async def do_mute(bot: Bot, chat_id: int, uid: int, until: datetime | None = None) -> None:
    await bot.restrict_chat_member(chat_id, uid, permissions=MUTED, until_date=until)


async def do_unmute(bot: Bot, chat_id: int, uid: int) -> None:
    chat = await bot.get_chat(chat_id)
    await bot.restrict_chat_member(chat_id, uid, permissions=chat.permissions or FULL)


async def log_action(
    session: AsyncSession, tenant: Tenant, action: str, target_id: int, moderator_id: int | None,
    reason: str = "", until: datetime | None = None,
) -> None:
    session.add(ModAction(tenant_id=tenant.id, action=action, target_id=target_id,
                          moderator_id=moderator_id, reason=reason[:256], until=until))
    await tenants_repo.add_audit(session, f"mod_{action}", moderator_id, tenant.id, {"target": target_id})


async def warning_count(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    return int((await session.execute(
        select(func.count()).select_from(ModWarning)
        .where(ModWarning.tenant_id == tenant_id, ModWarning.user_id == user_id)
    )).scalar_one())


async def apply_warn(
    session: AsyncSession, bot: Bot, tenant: Tenant, target_id: int, moderator_id: int | None, reason: str
) -> tuple[int, str | None]:
    """Add a warning and run the tenant's configured threshold action. Returns (count, action)."""
    session.add(ModWarning(tenant_id=tenant.id, user_id=target_id, moderator_id=moderator_id, reason=reason[:256]))
    await session.flush()
    count = await warning_count(session, tenant.id, target_id)
    cfg = mod_cfg(tenant)
    action = cfg["warn_actions"].get(str(count))
    if action not in ("mute", "kick", "ban"):
        return count, None
    try:
        until = None
        if action == "mute":
            until = utcnow() + timedelta(hours=int(cfg["warn_mute_hours"]))
            await do_mute(bot, tenant.chat_id, target_id, until)
        elif action == "kick":
            await do_kick(bot, tenant.chat_id, target_id)
        else:
            await do_ban(bot, tenant.chat_id, target_id)
        await log_action(session, tenant, action, target_id, moderator_id, f"{count} warnings", until)
    except TelegramAPIError as exc:
        log.info("threshold action failed: %s", exc)
        return count, f"{action} (failed: bot needs admin rights)"
    return count, action
