from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import (
    automod,
    billing,
    callbacks,
    chat_events,
    commands,
    errors,
    group_events,
    payments,
    play,
    verification,
    vip,
    xp,
)
from app.bot.loader import load_modules
from app.bot.middlewares.db import DbSessionMiddleware
from app.config.settings import Settings
from app.services.cache import Cache


def build_dispatcher(settings: Settings, cache: Cache) -> Dispatcher:
    load_modules()
    storage: BaseStorage = (
        RedisStorage.from_url(settings.redis_url) if cache.redis_ok and settings.redis_url else MemoryStorage()
    )
    dp = Dispatcher(storage=storage, settings=settings, cache=cache)
    dp.update.outer_middleware(DbSessionMiddleware())
    # Order matters: auto-moderation sees group messages first; multi-step flows (FSM) run before the
    # generic command dispatcher; keyword/join handlers run last.
    for router in (
        automod.router, xp.router, payments.router, chat_events.router, billing.router, vip.router,
        verification.router, play.router, callbacks.router, commands.router, group_events.router,
    ):
        dp.include_router(router)
    dp.errors.register(errors.on_error)
    return dp
