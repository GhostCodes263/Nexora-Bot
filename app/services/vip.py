from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import VipMembership
from app.repositories import tenants as tenants_repo
from app.services import config
from app.utils.time import aware, utcnow

log = logging.getLogger(__name__)
PERIOD_DAYS = {"m": 30, "y": 365}


async def channel_id(session: AsyncSession, settings: Settings) -> int:
    cfg = await config.vip(session)
    return int(cfg.get("channel_id") or settings.vip_channel_id or 0)


async def get(session: AsyncSession, user_id: int) -> VipMembership | None:
    return await session.get(VipMembership, user_id)


def is_active(m: VipMembership | None) -> bool:
    return m is not None and m.status in ("active", "grace")


async def activate(session: AsyncSession, user_id: int, period: str, days: int | None = None) -> VipMembership:
    now = utcnow()
    add = timedelta(days=days if days is not None else PERIOD_DAYS[period])
    m = await get(session, user_id)
    if m is None:
        m = VipMembership(user_id=user_id, status="active", period=period, started_at=now,
                          expires_at=now + add, reminders=[])
        session.add(m)
    else:
        base = aware(m.expires_at) if is_active(m) and aware(m.expires_at) > now else now
        m.expires_at = base + add
        m.status = "active"
        m.period = period
        m.grace_until = None
        m.reminders = []
    return m


async def invite_link(bot: Bot, channel: int) -> str | None:
    """Join-request link: access is granted only after we verify active VIP status."""
    try:
        link = await bot.create_chat_invite_link(channel, name="VIP access", creates_join_request=True)
        return link.invite_link
    except TelegramAPIError as exc:
        log.warning("invite link failed: %s", exc)
        return None


async def revoke_access(bot: Bot, channel: int, user_id: int) -> None:
    try:
        await bot.ban_chat_member(channel, user_id)
        await bot.unban_chat_member(channel, user_id, only_if_banned=True)
    except TelegramAPIError as exc:
        log.warning("VIP revoke failed for %s: %s", user_id, exc)


async def _dm(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        pass


async def process_expiries(session: AsyncSession, bot: Bot, settings: Settings) -> None:
    cfg = await config.vip(session)
    ch = await channel_id(session, settings)
    now = utcnow()
    rows = (await session.execute(
        select(VipMembership).where(VipMembership.status.in_(("active", "grace")))
    )).scalars().all()
    for m in rows:
        exp = aware(m.expires_at)
        if m.status == "active" and exp <= now:
            if int(cfg["grace_days"]) > 0:
                m.status = "grace"
                m.grace_until = exp + timedelta(days=int(cfg["grace_days"]))
                await _dm(bot, m.user_id, f"⌛ Your VIP expired. You keep access until {aware(m.grace_until):%Y-%m-%d}. Use /vip to renew.")
            else:
                m.status = "expired"
                if ch:
                    await revoke_access(bot, ch, m.user_id)
                await _dm(bot, m.user_id, "⛔ Your VIP has ended. Use /vip to renew.")
            await tenants_repo.add_audit(session, "vip_" + m.status, None, None, {"user": m.user_id})
        elif m.status == "grace" and m.grace_until is not None and aware(m.grace_until) <= now:
            m.status = "expired"
            if ch:
                await revoke_access(bot, ch, m.user_id)
            await _dm(bot, m.user_id, "⛔ Your VIP grace period ended and channel access was removed. Use /vip to renew.")
            await tenants_repo.add_audit(session, "vip_expired", None, None, {"user": m.user_id})
        elif m.status == "active":
            marks = list(m.reminders or [])
            fresh = False
            for d in cfg["reminder_days"]:
                key = f"d{d}"
                if exp - now <= timedelta(days=int(d)) and key not in marks:
                    marks.append(key)
                    fresh = True
            if fresh:
                m.reminders = marks
                await _dm(bot, m.user_id, f"⏰ Your VIP ends on {exp:%Y-%m-%d}. Use /vip to renew.")


async def eligibility_error(session: AsyncSession, user_id: int) -> str | None:
    """None if the user may buy VIP, otherwise a message explaining why not."""
    from app.repositories import verification as verification_repo

    cfg = await config.vip(session)
    if cfg["require_verification"] and not await verification_repo.is_approved(session, user_id):
        return "VIP is for verified members. Apply with /verify and come back once you're approved."
    return None
