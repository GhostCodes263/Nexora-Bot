from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import billing as kb
from app.bot.keyboards.billing import Bill
from app.config.settings import Settings
from app.database.models import Tenant, User
from app.repositories import tenants as tenants_repo
from app.services import billing, config, plans
from app.services import payments as pay
from app.services.cache import Cache
from app.services.permissions import resolve_role
from app.services.roles import Role

log = logging.getLogger(__name__)
router = Router(name="billing")
GROUPS = {"group", "supergroup"}


class Bcast(CallbackData, prefix="bc"):
    a: str  # go | no
    id: str


async def _edit(cb: CallbackQuery, view: kb.View) -> None:
    try:
        await cb.message.edit_text(view[0], reply_markup=view[1])  # type: ignore[union-attr]
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


@router.callback_query(Bill.filter())
async def on_bill(
    cb: CallbackQuery, callback_data: Bill, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings
) -> None:
    msg = cb.message
    if msg is None or not hasattr(msg, "chat") or msg.chat.type not in GROUPS:  # type: ignore[union-attr]
        await cb.answer("Open /plans inside your group to manage its subscription.", show_alert=True)
        return
    tenant = await tenants_repo.get_by_chat_id(session, msg.chat.id)  # type: ignore[union-attr]
    if tenant is None:
        await cb.answer()
        return
    role = await resolve_role(session, bot, cache, settings, cb.from_user.id, tenant)
    if role < Role.ADMIN:
        await cb.answer("Only group admins can manage the subscription.", show_alert=True)
        return
    cfg = await config.billing(session)
    a, code, period = callback_data.a, callback_data.p, callback_data.t

    if a == "plans":
        current = (await plans.effective_view(session, cache, tenant)).code
        await _edit(cb, kb.plans_view(await plans.list_plans(session), current, int(cfg["trial_days"])))
        await cb.answer()
        return

    plan = await plans.get_plan(session, code)
    if plan is None or not plan.is_active or plan.price_month_cents <= 0:
        await cb.answer("That plan is not available.", show_alert=True)
        return

    if a == "pick":
        await _edit(cb, kb.pick_view(plan, int(cfg["trial_days"])))
        await cb.answer()
    elif a == "trial":
        ok, text = await billing.start_trial(session, cache, tenant, cb.from_user.id, code)
        await cb.answer()
        await msg.answer(text)  # type: ignore[union-attr]
    elif a == "buy" and period in ("m", "y"):
        amount = plan.stars_month if period == "m" else plan.stars_year
        provider = pay.get_provider()
        try:
            await provider.send_invoice(
                bot, cb.from_user.id, f"{plan.name} · {'monthly' if period == 'm' else 'yearly'}",
                f"{plan.name} plan for {tenant.title or 'your group'}", pay.rent_payload(tenant.id, code, period), amount,
            )
        except TelegramForbiddenError:
            me = await bot.me()
            await cb.answer(f"Open a private chat with @{me.username}, press Start, then tap Buy again.", show_alert=True)
            return
        except TelegramAPIError as exc:
            log.warning("invoice failed: %s", exc)
            await cb.answer("I couldn't create the invoice. Try again in a moment.", show_alert=True)
            return
        await cb.answer("📨 Invoice sent to your private chat with me.", show_alert=True)
    else:
        await cb.answer()


@router.callback_query(Bcast.filter())
async def on_broadcast(
    cb: CallbackQuery, callback_data: Bcast, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings
) -> None:
    if not settings.owner_id or cb.from_user.id != settings.owner_id:
        await cb.answer("Not allowed.", show_alert=True)
        return
    raw = await cache.get(f"bc:{callback_data.id}")
    if raw is None:
        await cb.answer("This broadcast expired. Create it again.", show_alert=True)
        return
    await cache.delete(f"bc:{callback_data.id}")
    if callback_data.a == "no":
        await cb.answer("Cancelled.")
        await cb.message.edit_text("Broadcast cancelled.")  # type: ignore[union-attr]
        return
    data = json.loads(raw)
    await cb.answer("Sending…")
    if data["target"] == "groups":
        ids = [t.chat_id for t in (await session.execute(
            select(Tenant).where(Tenant.is_active.is_(True), Tenant.chat_type.in_(("group", "supergroup", "channel")))
        )).scalars()]
    else:
        ids = list((await session.execute(select(User.id).where(User.is_globally_banned.is_(False)))).scalars())
    ok = failed = 0
    for chat_id in ids:
        try:
            await bot.send_message(chat_id, data["text"])
            ok += 1
        except TelegramAPIError:
            failed += 1
        await asyncio.sleep(0.05)  # stay well below Telegram's rate limits
    await tenants_repo.add_audit(session, "broadcast", cb.from_user.id, None, {"target": data["target"], "ok": ok, "failed": failed})
    await cb.message.edit_text(f"📣 Broadcast finished: {ok} delivered, {failed} failed.")  # type: ignore[union-attr]


def broadcast_keyboard(bid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Send", callback_data=Bcast(a="go", id=bid).pack()),
        InlineKeyboardButton(text="✖️ Cancel", callback_data=Bcast(a="no", id=bid).pack()),
    ]])
