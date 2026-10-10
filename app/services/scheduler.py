from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import CaptchaPending, ScheduledMessage, Tenant
from app.database.session import session_maker
from app.services import billing, vip
from app.services.cache import Cache
from app.utils.time import utcnow

log = logging.getLogger(__name__)
INTERVAL = 30


async def send_scheduled(session: AsyncSession, bot: Bot) -> None:
    now = utcnow()
    rows = (await session.execute(
        select(ScheduledMessage).where(ScheduledMessage.sent.is_(False), ScheduledMessage.run_at <= now).limit(50)
    )).scalars().all()
    for m in rows:
        m.sent = True  # mark first: a failing chat must never be retried forever
        try:
            await bot.send_message(m.chat_id, m.text)
        except TelegramAPIError as exc:
            log.info("scheduled message %s failed: %s", m.id, exc)


async def expire_captchas(session: AsyncSession, bot: Bot) -> None:
    now = utcnow()
    rows = (await session.execute(
        select(CaptchaPending).where(CaptchaPending.expires_at <= now).limit(100)
    )).scalars().all()
    for row in rows:
        tenant = await session.get(Tenant, row.tenant_id)
        if tenant is not None:
            try:
                await bot.ban_chat_member(tenant.chat_id, row.user_id)
                await bot.unban_chat_member(tenant.chat_id, row.user_id, only_if_banned=True)
                if row.message_id:
                    await bot.delete_message(tenant.chat_id, row.message_id)
            except TelegramAPIError as exc:
                log.info("captcha kick failed: %s", exc)
        await session.delete(row)


async def _job(name: str, fn: Callable[[AsyncSession], Awaitable[None]]) -> None:
    try:
        async with session_maker()() as session:
            await fn(session)
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("scheduler job %s failed", name)


async def tick(bot: Bot, cache: Cache, settings: Settings) -> None:
    await _job("rentals", lambda s: billing.process_expiries(s, bot, cache))
    await _job("vip", lambda s: vip.process_expiries(s, bot, settings))
    await _job("scheduled", lambda s: send_scheduled(s, bot))
    await _job("captcha", lambda s: expire_captchas(s, bot))


async def run_scheduler(bot: Bot, cache: Cache, settings: Settings, stop: asyncio.Event) -> None:
    log.info("Scheduler started")
    while not stop.is_set():
        # Lock so that two running instances never process the same expiries twice.
        if await cache.set_nx("sched:lock", "1", INTERVAL - 5):
            await tick(bot, cache, settings)
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL)
        except TimeoutError:
            pass
