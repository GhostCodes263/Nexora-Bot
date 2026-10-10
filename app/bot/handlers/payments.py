from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import Tenant
from app.services import payments as pay
from app.services.cache import Cache

log = logging.getLogger(__name__)
router = Router(name="payments")


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, session: AsyncSession) -> None:
    """Telegram asks us to confirm before charging. We re-validate price and eligibility from the DB."""
    error = await pay.validate_checkout(session, q.invoice_payload, q.total_amount, q.currency, q.from_user.id)
    if error:
        await q.answer(ok=False, error_message=error)
    else:
        await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings
) -> None:
    sp = message.successful_payment
    if sp is None or message.from_user is None:
        return
    parsed = pay.parse_payload(sp.invoice_payload)
    if parsed is None:
        log.error("Paid but unknown payload (charge %s)", sp.telegram_payment_charge_id)
        await message.answer("Your payment was received but I couldn't match it to a product. "
                             "The owner has been notified and will sort it out.")
        if settings.owner_id:
            try:
                await bot.send_message(settings.owner_id,
                                       f"🚨 Unmatched payment from <code>{message.from_user.id}</code>, charge "
                                       f"<code>{escape(sp.telegram_payment_charge_id)}</code>, payload "
                                       f"<code>{escape(sp.invoice_payload)}</code>")
            except TelegramAPIError:
                pass
        return
    payment = await pay.record_payment(session, message.from_user.id, parsed, sp)
    if payment is None:
        log.info("Duplicate payment callback ignored (%s)", sp.telegram_payment_charge_id)
        return  # idempotent: the first callback already granted the purchase
    text = await pay.fulfill(session, bot, cache, payment, parsed, settings)
    await message.answer(text)
    if parsed["kind"] == "rent":
        tenant = await session.get(Tenant, parsed["tenant_id"])
        if tenant is not None:
            try:
                await bot.send_message(tenant.chat_id, f"💎 {escape(message.from_user.full_name)} activated "
                                                       f"<b>{escape(parsed['plan'].title())}</b> for this group. Thank you!")
            except TelegramAPIError:
                pass
