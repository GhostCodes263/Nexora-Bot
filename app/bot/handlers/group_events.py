from __future__ import annotations

import logging
from datetime import timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import CaptchaPending, MemberEvent, Tenant
from app.modules.management import service as ms
from app.modules.moderation import service as svc
from app.repositories import tenants as tenants_repo
from app.services import plans
from app.services.cache import Cache
from app.services.permissions import resolve_role
from app.services.roles import Role
from app.utils.telegram import safe_reply
from app.utils.time import utcnow

log = logging.getLogger(__name__)
router = Router(name="group_events")
GROUPS = {"group", "supergroup"}


class Cap(CallbackData, prefix="cap"):
    uid: int


async def _tenant(session: AsyncSession, chat_id: int) -> Tenant | None:
    t = await tenants_repo.get_by_chat_id(session, chat_id)
    return t if t is not None and t.is_active else None


@router.message(F.new_chat_members, F.chat.type.in_(GROUPS))
async def on_join(message: Message, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings) -> None:
    tenant = await _tenant(session, message.chat.id)
    if tenant is None:
        return
    plan = await plans.effective_view(session, cache, tenant)
    mg, mod = ms.mg_cfg(tenant), svc.mod_cfg(tenant)
    adder_role = Role.USER
    if message.from_user is not None:
        adder_role = await resolve_role(session, bot, cache, settings, message.from_user.id, tenant)
    try:
        count: int | str = await bot.get_chat_member_count(message.chat.id)
    except TelegramAPIError:
        count = ""
    newbies = []
    for u in message.new_chat_members or []:
        if u.id == bot.id:
            continue
        if u.is_bot:
            if mod["automod"]["antibot"] and plan.has("automod_advanced") and adder_role < Role.ADMIN:
                try:
                    await svc.do_kick(bot, message.chat.id, u.id)
                    await svc.log_action(session, tenant, "antibot", u.id, None, "bot added by non-admin")
                except TelegramAPIError as exc:
                    log.info("antibot kick failed: %s", exc)
            continue
        session.add(MemberEvent(tenant_id=tenant.id, user_id=u.id, kind="join"))
        newbies.append(u)

    if mg["captcha"]["on"] and plan.has("captcha"):
        minutes = int(mg["captcha"]["minutes"])
        for u in newbies:
            try:
                await svc.do_mute(bot, message.chat.id, u.id)
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ I'm human", callback_data=Cap(uid=u.id).pack())]])
                sent = await bot.send_message(
                    message.chat.id,
                    f'🤖 <a href="tg://user?id={u.id}">{escape(u.full_name)}</a>, press the button within '
                    f"{minutes} minutes to start chatting.", reply_markup=kb)
            except TelegramAPIError as exc:
                log.info("captcha could not start: %s", exc)
                continue
            await session.execute(delete(CaptchaPending).where(
                CaptchaPending.tenant_id == tenant.id, CaptchaPending.user_id == u.id))
            session.add(CaptchaPending(tenant_id=tenant.id, user_id=u.id, message_id=sent.message_id,
                                       expires_at=utcnow() + timedelta(minutes=minutes)))
        return

    if mg["welcome"]["on"] and plan.has("welcome"):
        for u in newbies[:5]:
            await safe_reply(message, ms.render(mg["welcome"]["text"], u.id, u.full_name, tenant.title, count))


@router.callback_query(Cap.filter())
async def on_captcha(cb: CallbackQuery, callback_data: Cap, bot: Bot, session: AsyncSession, cache: Cache) -> None:
    msg = cb.message
    if msg is None or not hasattr(msg, "chat"):
        await cb.answer()
        return
    if cb.from_user.id != callback_data.uid:
        await cb.answer("This button is for the new member only.", show_alert=True)
        return
    tenant = await _tenant(session, msg.chat.id)  # type: ignore[union-attr]
    if tenant is None:
        await cb.answer()
        return
    try:
        await svc.do_unmute(bot, tenant.chat_id, cb.from_user.id)
    except TelegramAPIError as exc:
        await cb.answer(f"Could not unmute: {exc}", show_alert=True)
        return
    await session.execute(delete(CaptchaPending).where(
        CaptchaPending.tenant_id == tenant.id, CaptchaPending.user_id == cb.from_user.id))
    await cb.answer("Welcome! ✅")
    try:
        await msg.delete()  # type: ignore[union-attr]
    except TelegramAPIError:
        pass
    mg = ms.mg_cfg(tenant)
    plan = await plans.effective_view(session, cache, tenant)
    if mg["welcome"]["on"] and plan.has("welcome"):
        text = ms.render(mg["welcome"]["text"], cb.from_user.id, cb.from_user.full_name, tenant.title)
        try:
            await bot.send_message(tenant.chat_id, text)
        except TelegramAPIError:
            await bot.send_message(tenant.chat_id, text, parse_mode=None)


@router.message(F.left_chat_member, F.chat.type.in_(GROUPS))
async def on_leave(message: Message, bot: Bot, session: AsyncSession, cache: Cache) -> None:
    tenant = await _tenant(session, message.chat.id)
    u = message.left_chat_member
    if tenant is None or u is None or u.id == bot.id or u.is_bot:
        return
    session.add(MemberEvent(tenant_id=tenant.id, user_id=u.id, kind="leave"))
    await session.execute(delete(CaptchaPending).where(
        CaptchaPending.tenant_id == tenant.id, CaptchaPending.user_id == u.id))
    mg = ms.mg_cfg(tenant)
    plan = await plans.effective_view(session, cache, tenant)
    if mg["goodbye"]["on"] and plan.has("welcome"):
        await safe_reply(message, ms.render(mg["goodbye"]["text"], u.id, u.full_name, tenant.title))


@router.message(F.text, F.chat.type.in_(GROUPS))
async def on_keyword(message: Message, bot: Bot, session: AsyncSession, cache: Cache) -> object:
    text = message.text or ""
    if text.startswith("/") or message.from_user is None or message.from_user.is_bot:
        return UNHANDLED
    tenant = await _tenant(session, message.chat.id)
    if tenant is None:
        return UNHANDLED
    rows = await ms.get_triggers(session, cache, tenant.id)
    if not rows:
        return UNHANDLED
    plan = await plans.effective_view(session, cache, tenant)
    if not plan.has("auto_replies"):
        return UNHANDLED
    import re

    low = text.lower()
    for keyword, response in rows:
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", low):
            if await cache.set_nx(f"trig:{tenant.id}:{keyword}", "1", 20):
                await safe_reply(message, response)
            return None
    return UNHANDLED
