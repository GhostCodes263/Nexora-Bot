from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states.verification import Apply, ReviewNote
from app.config.settings import Settings
from app.database.models import User, VerificationApplication
from app.modules.verification import flow
from app.modules.verification.flow import Ver
from app.repositories import verification as repo
from app.services.cache import Cache
from app.services.permissions import resolve_role

log = logging.getLogger(__name__)
router = Router(name="verification")


async def ask_next(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    qs = await flow.questions(session)
    idx = int(data.get("idx", 0))
    await message.answer(qs[idx]["text"])


@router.message(Apply.answering)
async def on_answer(message: Message, bot: Bot, session: AsyncSession, state: FSMContext, settings: Settings) -> object:
    if message.text and message.text.startswith("/"):
        return UNHANDLED  # commands such as /cancelverify go to the normal dispatcher
    if message.from_user is None:
        return None
    data = await state.get_data()
    qs = await flow.questions(session)
    idx = int(data.get("idx", 0))
    if idx >= len(qs):
        await state.clear()
        return None
    q = qs[idx]
    photo_id = message.photo[-1].file_id if message.photo else None
    ok, value, err = flow.validate(q, message.text, bool(photo_id))
    if not ok:
        await message.answer(err)
        return None
    answers = dict(data.get("answers", {}))
    if q["kind"] == "age" and int(value or 0) < flow.MIN_AGE:
        await state.clear()
        await message.answer(f"Sorry, this community is {flow.MIN_AGE}+ only, so I can't take your application. "
                             "Nothing you typed was saved.")
        return None
    if q["kind"] == "photo":
        await state.update_data(photo=photo_id)
    else:
        answers[q["key"]] = value
    idx += 1
    await state.update_data(idx=idx, answers=answers)
    if idx < len(qs):
        await message.answer(qs[idx]["text"])
        return None
    data = await state.get_data()
    await state.clear()
    app = await repo.create(session, message.from_user.id, answers, data.get("photo"))
    await message.answer(f"✅ Application submitted! Your verification ID is <b>{app.public_id}</b>.\n"
                         "A reviewer will look at it soon. Check progress with /vstatus.")
    await flow.notify_reviewers(session, bot, settings, app, "New application")
    return None


@router.message(Apply.more_info)
async def on_more_info(message: Message, bot: Bot, session: AsyncSession, state: FSMContext, settings: Settings) -> object:
    if message.text and message.text.startswith("/"):
        return UNHANDLED
    if message.from_user is None or not message.text:
        await message.answer("Please send your answer as text.")
        return None
    app = await repo.latest(session, message.from_user.id)
    await state.clear()
    if app is None or app.status != "NEEDS_MORE_INFORMATION":
        await message.answer("There is nothing waiting for more information right now.")
        return None
    answers = dict(app.answers or {})
    answers["additional_info"] = [*answers.get("additional_info", []), message.text.strip()[:500]]
    app.answers = answers
    await repo.set_status(session, app, "PENDING", message.from_user.id, "applicant added information")
    await message.answer("Thanks! Your application is back in the queue.")
    await flow.notify_reviewers(session, bot, settings, app, "Applicant added information")
    return None


async def _reviewer_ok(cb_user: int, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings) -> bool:
    role = await resolve_role(session, bot, cache, settings, cb_user, None)
    return await repo.is_reviewer(session, cb_user, role)


async def _show(cb: CallbackQuery, session: AsyncSession, app: VerificationApplication) -> None:
    user = await session.get(User, app.user_id)
    try:
        await cb.message.edit_text(flow.review_text(app, user.username if user else None),  # type: ignore[union-attr]
                                   reply_markup=flow.review_kb(app))
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


@router.callback_query(Ver.filter())
async def on_review(
    cb: CallbackQuery, callback_data: Ver, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings,
    state: FSMContext,
) -> None:
    msg = cb.message
    if msg is None or not hasattr(msg, "chat") or msg.chat.type != "private":  # type: ignore[union-attr]
        await cb.answer("Review applications in a private chat with me.", show_alert=True)
        return
    # Every press re-checks that the PRESSER is a reviewer; the button alone grants nothing.
    if not await _reviewer_ok(cb.from_user.id, bot, session, cache, settings):
        await cb.answer("Not allowed.", show_alert=True)
        return
    app = await session.get(VerificationApplication, callback_data.i)
    if app is None:
        await cb.answer("Application not found (it may have been deleted).", show_alert=True)
        return
    a, actor = callback_data.a, cb.from_user.id
    is_owner = bool(settings.owner_id) and actor == settings.owner_id
    if app.status == "SUSPENDED" and not is_owner and a != "open":
        await cb.answer("Suspended applications can only be changed by the owner.", show_alert=True)
        return

    if a == "open":
        await _show(cb, session, app)
    elif a == "rev":
        await repo.set_status(session, app, "UNDER_REVIEW", actor)
        await _show(cb, session, app)
    elif a == "flag":
        app.flagged = not app.flagged
        await repo.add_action(session, app, actor, "FLAGGED" if app.flagged else "UNFLAGGED")
        await _show(cb, session, app)
    elif a == "photo":
        if app.photo_file_id:
            await msg.answer_photo(app.photo_file_id, caption=app.public_id)  # type: ignore[union-attr]
    elif a == "ok":
        await repo.set_status(session, app, "APPROVED", actor)
        await flow.dm(bot, app.user_id, f"✅ <b>Approved!</b> Your verification {app.public_id} is complete. "
                                        "You can now unlock VIP with /vip.")
        await _show(cb, session, app)
    elif a in ("no", "info"):
        await state.set_state(ReviewNote.note)
        await state.update_data(app_id=app.id, action=a)
        await msg.answer("Send ONE message with the reason to give the applicant." if a == "no"
                         else "Send ONE message with what you need from the applicant.")
    await cb.answer()


@router.message(ReviewNote.note)
async def on_review_note(message: Message, bot: Bot, session: AsyncSession, state: FSMContext, cache: Cache, settings: Settings) -> object:
    if message.text and message.text.startswith("/"):
        return UNHANDLED
    if message.from_user is None or not message.text:
        await message.answer("Please send text.")
        return None
    if not await _reviewer_ok(message.from_user.id, bot, session, cache, settings):
        await state.clear()
        return None
    data = await state.get_data()
    await state.clear()
    app = await session.get(VerificationApplication, int(data.get("app_id", 0)))
    if app is None:
        await message.answer("Application not found.")
        return None
    note = message.text.strip()[:500]
    if data.get("action") == "no":
        await repo.set_status(session, app, "REJECTED", message.from_user.id, note)
        await flow.dm(bot, app.user_id, f"❌ Your application {app.public_id} was not approved.\nReason: {note}\n"
                                        "You may apply again after 7 days with /verify.")
    else:
        await repo.set_status(session, app, "NEEDS_MORE_INFORMATION", message.from_user.id, note)
        await flow.dm(bot, app.user_id, f"ℹ️ Your application {app.public_id} needs more information:\n{note}\n"
                                        "Send /verify to answer.")
    await message.answer(f"Saved: {app.public_id} → {app.status}.", reply_markup=flow.review_kb(app))
    return None
