from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from app.config.settings import Settings
from app.services.cache import Cache
from app.utils.errors import record_error

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent, bot: Bot, cache: Cache, settings: Settings) -> bool:
    exc = event.exception
    log.error("Unhandled error in update handler", exc_info=exc)
    record_error("update", exc)

    update = event.update
    try:
        if update.message is not None:
            await update.message.reply("⚠️ Something went wrong. The team has been notified.")
        elif update.callback_query is not None:
            await update.callback_query.answer("Something went wrong.", show_alert=True)
    except TelegramAPIError:
        pass

    # Notify the owner, at most once a minute so an outage cannot flood them.
    if settings.owner_id and await cache.set_nx("errnotify", "1", 60):
        try:
            await bot.send_message(
                settings.owner_id,
                f"🚨 <b>Error</b>: <code>{escape(type(exc).__name__)}</code> "
                f"{escape(str(exc)[:300])}\nSee /errors and the service logs.",
            )
        except TelegramAPIError:
            pass
    return True
