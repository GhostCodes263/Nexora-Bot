from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.services import config
from app.services import payments as pay
from app.services import vip as vip_service

log = logging.getLogger(__name__)
router = Router(name="vip")


class VipBuy(CallbackData, prefix="vipbuy"):
    p: str  # m | y


def vip_keyboard(cfg: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Monthly · {cfg['monthly_stars']} ⭐", callback_data=VipBuy(p="m").pack())],
        [InlineKeyboardButton(text=f"Yearly · {cfg['annual_stars']} ⭐", callback_data=VipBuy(p="y").pack())],
    ])


@router.callback_query(VipBuy.filter())
async def on_vip_buy(cb: CallbackQuery, callback_data: VipBuy, bot: Bot, session: AsyncSession) -> None:
    error = await vip_service.eligibility_error(session, cb.from_user.id)
    if error:
        await cb.answer(error, show_alert=True)
        return
    cfg = await config.vip(session)
    period = callback_data.p
    if period not in ("m", "y"):
        await cb.answer()
        return
    amount = int(cfg["monthly_stars"] if period == "m" else cfg["annual_stars"])
    try:
        await pay.get_provider().send_invoice(
            bot, cb.from_user.id, f"VIP · {'monthly' if period == 'm' else 'yearly'}",
            "Access to the VIP channel", pay.vip_payload(period), amount)
    except TelegramForbiddenError:
        await cb.answer("Start a private chat with me first, then try again.", show_alert=True)
        return
    except TelegramAPIError as exc:
        log.warning("vip invoice failed: %s", exc)
        await cb.answer("I couldn't create the invoice. Try again shortly.", show_alert=True)
        return
    await cb.answer("📨 Invoice sent.")


@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest, session: AsyncSession, settings: Settings) -> None:
    """Approve a VIP-channel join request only if the person currently has VIP. Anyone else is declined."""
    channel = await vip_service.channel_id(session, settings)
    if not channel or req.chat.id != channel:
        return
    member = await vip_service.get(session, req.from_user.id)
    try:
        if vip_service.is_active(member):
            await req.approve()
        else:
            await req.decline()
    except TelegramAPIError as exc:
        log.info("join request handling failed: %s", exc)
