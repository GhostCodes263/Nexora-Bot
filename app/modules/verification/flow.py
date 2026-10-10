from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import VerificationApplication
from app.repositories import verification as repo
from app.services import config

log = logging.getLogger(__name__)

MIN_AGE = 18

BASE_QUESTIONS: list[dict] = [
    {"key": "name", "text": "1️⃣ What name should we use for you?", "kind": "text", "max": 60},
    {"key": "age", "text": f"2️⃣ How old are you? This community is for adults only ({MIN_AGE}+).", "kind": "age"},
    {"key": "gender", "text": "3️⃣ What is your gender?", "kind": "text", "max": 30},
    {"key": "country", "text": "4️⃣ Which country are you in? (Country level is enough. No exact address.)",
     "kind": "text", "max": 60},
    {"key": "bio", "text": "5️⃣ Tell us a little about yourself.", "kind": "text", "max": 500},
    {"key": "looking_for", "text": "6️⃣ What are you looking for / your dating preferences? (or send 'skip')",
     "kind": "optional", "max": 200},
]
PHOTO_QUESTION = {"key": "photo", "text": "📸 Send one clear profile photo of yourself.", "kind": "photo"}


class Ver(CallbackData, prefix="ver"):
    a: str  # open | rev | flag | photo | ok | no | info
    i: int = 0


async def questions(session: AsyncSession) -> list[dict]:
    cfg = await config.verification(session)
    qs = list(BASE_QUESTIONS)
    for n, text in enumerate(cfg.get("extra_questions", []), start=1):
        qs.append({"key": f"extra{n}", "text": f"➕ {text}", "kind": "optional", "max": 300, "label": text})
    if cfg.get("require_photo"):
        qs.append(PHOTO_QUESTION)
    return qs


def validate(question: dict, text: str | None, has_photo: bool) -> tuple[bool, str | None, str]:
    """Return (ok, cleaned value, error message)."""
    kind = question["kind"]
    if kind == "photo":
        return (True, "photo", "") if has_photo else (False, None, "Please send a photo (a picture, not a file).")
    value = (text or "").strip()
    if not value:
        return False, None, "Please answer with text."
    if kind == "age":
        if not value.isdigit() or not 10 <= int(value) <= 99:
            return False, None, "Please send your age as a number, for example 27."
        return True, value, ""
    if kind == "optional" and value.lower() == "skip":
        return True, "", ""
    return True, value[: question.get("max", 200)], ""


def review_text(app: VerificationApplication, username: str | None = None) -> str:
    lines = [f"<b>📝 Application {app.public_id}</b>", f"Status: <b>{app.status}</b>"
             + (" 🚩" if app.flagged else ""), f"Applicant ID: <code>{app.user_id}</code>"
             + (f" @{escape(username)}" if username else ""), ""]
    for key, value in (app.answers or {}).items():
        if key == "additional_info":
            for n, extra in enumerate(value, start=1):
                lines.append(f"<b>Extra info {n}:</b> {escape(str(extra))}")
        elif value != "":
            lines.append(f"<b>{escape(key.replace('_', ' ').title())}:</b> {escape(str(value))}")
    lines.append(f"Photo: {'yes' if app.photo_file_id else 'no'}")
    if app.reviewer_note:
        lines.append(f"\nLast note: {escape(app.reviewer_note)}")
    return "\n".join(lines)


def review_kb(app: VerificationApplication) -> InlineKeyboardMarkup:
    def b(text: str, a: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=Ver(a=a, i=app.id).pack())

    rows = [[b("✅ Approve", "ok"), b("❌ Reject", "no")],
            [b("ℹ️ Need info", "info"), b("👀 Under review", "rev")],
            [b("🚩 Flag", "flag")]]
    if app.photo_file_id:
        rows[2].append(b("🖼 Photo", "photo"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def notify_reviewers(session: AsyncSession, bot: Bot, settings: Settings, app: VerificationApplication, why: str) -> None:
    ids = {r.user_id for r in await repo.list_reviewers(session)}
    if settings.owner_id:
        ids.add(settings.owner_id)
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Open", callback_data=Ver(a="open", i=app.id).pack())]])
    for rid in ids:
        try:
            await bot.send_message(rid, f"🔔 {why}: <b>{app.public_id}</b>", reply_markup=markup)
        except TelegramAPIError:
            pass


async def dm(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError as exc:
        log.info("could not DM %s: %s", user_id, exc)
