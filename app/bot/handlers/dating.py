from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.play import rows
from app.bot.states.dating import DSetup
from app.database.models import DatingProfile, Tenant
from app.modules.dating import flow
from app.modules.dating.flow import Dt, Prop
from app.repositories import dating as repo
from app.services import gamestate
from app.services import tenants as tenant_service
from app.services.cache import Cache
from app.utils.time import utcnow

log = logging.getLogger(__name__)
router = Router(name="dating")


async def _send(bot: Bot, chat_id: int, text: str, photo: str | None = None, kb=None) -> None:
    try:
        if photo:
            await bot.send_photo(chat_id, photo, caption=text[:1020], reply_markup=kb)
        else:
            await bot.send_message(chat_id, text, reply_markup=kb)
    except TelegramAPIError as exc:
        log.info("dating DM to %s failed: %s", chat_id, exc)


async def dating_enabled(session: AsyncSession, tenant_id: int) -> bool:
    t = await session.get(Tenant, tenant_id)
    return t is not None and t.is_active and tenant_service.module_enabled(t, "dating")


async def pick_candidate(session: AsyncSession, me: DatingProfile) -> tuple[DatingProfile, int] | None:
    for p, tid in await repo.candidates(session, me):
        if await dating_enabled(session, tid) and flow.accepts(me, p) and flow.accepts(p, me):
            return p, tid
    return None


async def show_next(bot: Bot, session: AsyncSession, chat_id: int, me_id: int) -> None:
    me = await repo.get_profile(session, me_id)
    if me is None or me.opted_out:
        await _send(bot, chat_id, "Create your profile first with /dsetup.")
        return
    cand = await pick_candidate(session, me)
    if cand is None:
        await _send(bot, chat_id, "🙈 No more profiles right now. Join more groups' pools with /djoin, adjust /dpref, or check back later.")
        return
    p, tid = cand
    await _send(bot, chat_id, flow.card(p, flow.compat_score(me, p)), p.photo_file_id, flow.discover_kb(p.user_id, tid))


async def notify_match(bot: Bot, session: AsyncSession, a_id: int, b_id: int) -> None:
    pa, pb = await repo.get_profile(session, a_id), await repo.get_profile(session, b_id)
    if pa is None or pb is None:
        return
    for me, other, other_id in ((pa, pb, b_id), (pb, pa, a_id)):
        contact = (f'\n\n💬 <a href="tg://user?id={other_id}">Say hi to {escape(other.name)}</a>' if other.share_contact
                   else "\n\n(They prefer not to share contact details yet.)")
        await _send(bot, me.user_id, f"🎉 <b>It's a match!</b>\n\n{flow.card(other)}{contact}", other.photo_file_id)


async def answer_proposal(session: AsyncSession, cache: Cache, bot: Bot, uid: int, pid: str, accept: bool) -> str:
    state = await gamestate.load(cache, f"pp{pid}")
    if state is None or uid != state["b"]:
        return "That proposal has expired or isn't for you."
    await gamestate.drop(cache, f"pp{pid}")
    a, b, tid = state["a"], state["b"], state["tenant"]
    if not accept:
        await _send(bot, a, "💔 Your proposal was declined.")
        return "You declined the proposal."
    if await repo.couple_of(session, tid, a) or await repo.couple_of(session, tid, b):
        return "One of you is already in a couple here."
    await repo.create_couple(session, tid, a, b)
    pa, pb = await repo.get_profile(session, a), await repo.get_profile(session, b)
    await _send(bot, a, f"💞 <b>{escape(pb.name if pb else 'They')}</b> said yes! You're a couple now. Try /datenight.")
    return f"💞 You're now a couple with {escape(pa.name if pa else 'them')}! Try /datenight."


# --- profile wizard -----------------------------------------------------------------------------------------

@router.message(DSetup.answering)
async def on_setup_answer(message: Message, bot: Bot, session: AsyncSession, state: FSMContext) -> object:
    if message.text and message.text.startswith("/"):
        return UNHANDLED
    if message.from_user is None:
        return None
    data = await state.get_data()
    idx = int(data.get("idx", 0))
    q = flow.QUESTIONS[idx]
    photo_id = message.photo[-1].file_id if message.photo else None
    ok, value, err = flow.validate(q, message.text, bool(photo_id))
    if not ok:
        await message.answer(err)
        return None
    answers = dict(data.get("answers", {}))
    if q["kind"] == "age" and int(value or 0) < flow.MIN_AGE:
        await state.clear()
        await message.answer(f"Sorry, dating profiles are for {flow.MIN_AGE}+ only. Nothing was saved.")
        return None
    if q["kind"] == "photo":
        await state.update_data(photo=photo_id)
    elif q["key"] == "age":
        answers["age"] = int(value or 0)
    else:
        answers[q["key"]] = value or ""
    idx += 1
    await state.update_data(idx=idx, answers=answers)
    if idx < len(flow.QUESTIONS):
        await message.answer(flow.QUESTIONS[idx]["text"])
        return None
    final = await state.get_data()
    await state.clear()
    p = await repo.save_profile(session, message.from_user.id, answers, final.get("photo"))
    p.opted_out = False
    await message.answer("✅ <b>Profile saved!</b>\n1. In a group that enabled dating, send /djoin\n2. Then come here and send /discover\n"
                         "You control your privacy: /dhide, /doptout, /ddelete.\n\n" + flow.card(p))
    return None


# --- discover buttons -----------------------------------------------------------------------------------------------

@router.callback_query(Dt.filter())
async def on_dating(cb: CallbackQuery, callback_data: Dt, bot: Bot, session: AsyncSession, cache: Cache) -> None:
    msg = cb.message
    if msg is None or not hasattr(msg, "chat") or msg.chat.type != "private":  # type: ignore[union-attr]
        await cb.answer("Use /discover in a private chat with me.", show_alert=True)
        return
    me_id, target, tid = cb.from_user.id, callback_data.u, callback_data.t
    me = await repo.get_profile(session, me_id)
    them = await repo.get_profile(session, target)
    # Never trust the button's contents: re-check everything server-side.
    valid = (me is not None and not me.opted_out and them is not None and not them.hidden and not them.opted_out
             and await repo.in_pool(session, tid, me_id) and await repo.in_pool(session, tid, target)
             and not await repo.is_blocked(session, me_id, target) and await dating_enabled(session, tid))
    try:
        await msg.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    except TelegramAPIError:
        pass
    if not valid:
        await cb.answer("That profile is no longer available.")
        await show_next(bot, session, msg.chat.id, me_id)  # type: ignore[union-attr]
        return
    a = callback_data.a
    if a in ("lk", "cr"):
        matched = await repo.record_swipe(session, tid, me_id, target, "like" if a == "lk" else "crush")
        if matched:
            await notify_match(bot, session, me_id, target)
            await cb.answer("🎉 It's a match!")
        else:
            if a == "cr" and await cache.set_nx(f"crush:{me_id}:{target}", "1", 86400):
                await _send(bot, target, "🔥 Someone has a crush on you! Open /discover, they may show up.")
            await cb.answer("❤️ Sent" if a == "lk" else "🔥 Crush sent")
    elif a == "ps":
        await repo.record_swipe(session, tid, me_id, target, "dislike")
        await cb.answer()
    elif a == "bl":
        await repo.add_block(session, me_id, target)
        await repo.record_swipe(session, tid, me_id, target, "dislike")
        await cb.answer("🚫 Blocked. They'll never see you either.")
    elif a == "rp":
        await repo.add_report(session, me_id, target, tid, "reported from discover")
        await repo.add_block(session, me_id, target)
        await repo.record_swipe(session, tid, me_id, target, "dislike")
        await cb.answer("🚨 Reported and blocked. Thank you.", show_alert=True)
    await show_next(bot, session, msg.chat.id, me_id)  # type: ignore[union-attr]


@router.callback_query(Prop.filter())
async def on_proposal(cb: CallbackQuery, callback_data: Prop, bot: Bot, session: AsyncSession, cache: Cache) -> None:
    text = await answer_proposal(session, cache, bot, cb.from_user.id, callback_data.id, callback_data.a == "yes")
    try:
        await cb.message.edit_text(text)  # type: ignore[union-attr]
    except TelegramAPIError:
        pass
    await cb.answer()


def proposal_kb(pid: str):
    return rows([("💍 Yes!", Prop(a="yes", id=pid)), ("✖️ No thanks", Prop(a="no", id=pid))])


__all__ = ["router", "show_next", "notify_match", "answer_proposal", "proposal_kb", "utcnow"]
