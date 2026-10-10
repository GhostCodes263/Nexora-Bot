from __future__ import annotations

import time

from aiogram import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services.cache import Cache
from app.utils.time import human_duration

START = time.monotonic()
VERSION = "0.1.0"
MAINT_KEY = "maintenance"


def uptime() -> str:
    return human_duration(time.monotonic() - START)


async def is_maintenance(session: AsyncSession, cache: Cache) -> bool:
    cached = await cache.get(f"cfg:{MAINT_KEY}")
    if cached is not None:
        return cached == "1"
    value = bool((await tenants_repo.get_global(session, MAINT_KEY)).get("on", False))
    await cache.set(f"cfg:{MAINT_KEY}", "1" if value else "0", 15)
    return value


async def set_maintenance(session: AsyncSession, cache: Cache, on: bool) -> None:
    await tenants_repo.set_global(session, MAINT_KEY, {"on": on})
    await cache.set(f"cfg:{MAINT_KEY}", "1" if on else "0", 15)


async def stats_text(session: AsyncSession) -> str:
    users = await users_repo.count_users(session)
    groups = await tenants_repo.count_tenants(session, ("group", "supergroup"))
    channels = await tenants_repo.count_tenants(session, ("channel",))
    return (
        "<b>📊 Platform stats</b>\n"
        f"👥 Users: <b>{users}</b>\n"
        f"🏠 Groups: <b>{groups}</b>\n"
        f"📢 Channels: <b>{channels}</b>\n"
        f"⏱ Uptime: <b>{uptime()}</b>"
    )


async def health_text(session: AsyncSession, cache: Cache, bot: Bot, settings: Settings) -> str:
    t0 = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        db = f"✅ {int((time.perf_counter() - t0) * 1000)} ms"
    except Exception:  # noqa: BLE001
        db = "❌ error"
    t1 = time.perf_counter()
    try:
        await bot.get_me()
        tg = f"✅ {int((time.perf_counter() - t1) * 1000)} ms"
    except Exception:  # noqa: BLE001
        tg = "❌ error"
    redis = "✅ connected" if cache.redis_ok else "⚠️ memory fallback"
    mode = "webhook" if settings.use_webhook else "polling"
    return (
        "<b>🩺 System health</b>\n"
        f"Database: {db}\nTelegram API: {tg}\nRedis: {redis}\n"
        f"Mode: <code>{mode}</code>\nEnvironment: <code>{settings.environment}</code>\n"
        f"Version: <code>{VERSION}</code>\nUptime: {uptime()}"
    )
