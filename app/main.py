from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.bot.setup import build_dispatcher
from app.config.logging import setup_logging
from app.config.settings import get_settings
from app.database.session import dispose_engine, init_engine
from app.services.cache import Cache
from app.services.scheduler import run_scheduler

log = logging.getLogger("app")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def set_webhook_with_retry(bot: Bot, url: str, secret: str, allowed: list[str]) -> None:
    for attempt in range(1, 6):
        try:
            await bot.set_webhook(url, secret_token=secret, allowed_updates=allowed)
            log.info("Webhook set")
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("set_webhook attempt %s failed: %s", attempt, type(exc).__name__)
            await asyncio.sleep(2 * attempt)
    raise RuntimeError("Could not set the Telegram webhook")


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    if not settings.bot_token:
        sys.exit("BOT_TOKEN is not set.")
    if not settings.owner_id:
        log.warning("OWNER_ID is not set: nobody will have owner permissions")

    init_engine(settings.database_url)
    cache = Cache(settings.redis_url)
    await cache.connect()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, cache)
    allowed = dp.resolve_used_update_types()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner: web.AppRunner | None = None
    polling: asyncio.Task | None = None
    scheduler: asyncio.Task | None = None
    try:
        try:
            await bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in
                 (("start", "Start"), ("menu", "Main menu"), ("help", "Browse commands"),
                  ("settings", "Group settings"))]
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("set_my_commands failed: %s", type(exc).__name__)

        scheduler = asyncio.create_task(run_scheduler(bot, cache, settings, stop))
        if settings.use_webhook:
            SimpleRequestHandler(
                dispatcher=dp, bot=bot, secret_token=settings.webhook_secret_token
            ).register(app, path=settings.webhook_path)
            setup_application(app, dp, bot=bot)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.port).start()
            await set_webhook_with_retry(
                bot, settings.public_url + settings.webhook_path,
                settings.webhook_secret_token, allowed,
            )
            log.info("Running in webhook mode on port %s", settings.port)
            await stop.wait()
        else:
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.port).start()
            await bot.delete_webhook(drop_pending_updates=False)
            log.info("Running in polling mode")
            polling = asyncio.create_task(dp.start_polling(bot, allowed_updates=allowed))
            await stop.wait()
    finally:
        log.info("Shutting down")
        if scheduler is not None:
            scheduler.cancel()
        if polling is not None:
            polling.cancel()
        if runner is not None:
            await runner.cleanup()
        await bot.session.close()
        await cache.close()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(run())
