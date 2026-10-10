from __future__ import annotations

import math
import re
from datetime import datetime
from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import Couple, DatingProfile
from app.utils.time import aware

MIN_AGE = 18

QUESTIONS: list[dict] = [
    {"key": "name", "text": "1️⃣ What name should show on your profile?", "kind": "text", "max": 60},
    {"key": "age", "text": f"2️⃣ How old are you? ({MIN_AGE}+ only)", "kind": "age"},
    {"key": "gender", "text": "3️⃣ Your gender?", "kind": "text", "max": 30},
    {"key": "location", "text": "4️⃣ Where are you? <b>Country or city only</b>, never an exact address.", "kind": "text", "max": 60},
    {"key": "interests", "text": "5️⃣ Your interests, separated by commas (e.g. music, hiking, chess).", "kind": "text", "max": 200},
    {"key": "bio", "text": "6️⃣ A short bio.", "kind": "text", "max": 400},
    {"key": "looking_for", "text": "7️⃣ What are you looking for? (e.g. friendship, relationship)", "kind": "text", "max": 100},
    {"key": "rel_status", "text": "8️⃣ Relationship status? (or 'skip')", "kind": "optional", "max": 30},
    {"key": "photo", "text": "📸 Send a profile photo (or 'skip'). Only people in your groups' dating pools will see it.", "kind": "photo"},
]


class Dt(CallbackData, prefix="dt"):
    a: str  # lk | ps | cr | bl | rp
    u: int = 0  # target user id
    t: int = 0  # tenant (pool) id


class Prop(CallbackData, prefix="pr"):
    a: str  # yes | no
    id: str


def validate(q: dict, text: str | None, has_photo: bool) -> tuple[bool, str | None, str]:
    kind = q["kind"]
    value = (text or "").strip()
    if kind == "photo":
        if has_photo:
            return True, "photo", ""
        return (True, "", "") if value.lower() == "skip" else (False, None, "Send a photo, or type 'skip'.")
    if not value:
        return False, None, "Please answer with text."
    if kind == "age":
        if not value.isdigit() or not 10 <= int(value) <= 99:
            return False, None, "Please send your age as a number, for example 27."
        return True, value, ""
    if kind == "optional" and value.lower() == "skip":
        return True, "", ""
    return True, value[: q.get("max", 100)], ""


# --- matching helpers (pure) -------------------------------------------------------------------------------

def gender_key(text: str) -> str:
    t = text.strip().lower()
    if t in ("m", "male", "man", "boy"):
        return "male"
    if t in ("f", "female", "woman", "girl"):
        return "female"
    return "other"


def accepts(viewer: DatingProfile, other: DatingProfile) -> bool:
    """Does `viewer`'s preference (gender and age range) accept `other`?"""
    if viewer.pref_gender != "any" and viewer.pref_gender != gender_key(other.gender):
        return False
    return viewer.pref_min_age <= other.age <= viewer.pref_max_age


def interests_set(text: str) -> set[str]:
    return {t.strip().lower() for t in re.split(r"[,;/\n]+", text) if t.strip()}


def compat_score(a: DatingProfile, b: DatingProfile) -> int:
    ia, ib = interests_set(a.interests), interests_set(b.interests)
    union = ia | ib
    interest_pts = 50 * len(ia & ib) / len(union) if union else 0
    age_pts = max(0, 20 - abs(a.age - b.age) * 2)
    la, lb = a.looking_for.strip().lower(), b.looking_for.strip().lower()
    goal_pts = 20 if la and la == lb else 10 if la and lb else 0
    place_pts = 10 if a.location.strip() and a.location.strip().lower() == b.location.strip().lower() else 0
    return min(100, round(interest_pts + age_pts + goal_pts + place_pts))


def card(p: DatingProfile, score: int | None = None) -> str:
    lines = [f"<b>{escape(p.name)}</b>, {p.age}" + (f" · {escape(p.gender)}" if p.gender else "")]
    if p.location:
        lines.append(f"📍 {escape(p.location)}")
    if p.interests:
        lines.append(f"🎯 {escape(p.interests)}")
    if p.bio:
        lines.append(f"\n{escape(p.bio)}")
    if p.looking_for:
        lines.append(f"\n🔎 Looking for: {escape(p.looking_for)}")
    if p.rel_status:
        lines.append(f"💬 Status: {escape(p.rel_status)}")
    if score is not None:
        lines.append(f"\n💘 Compatibility: <b>{score}%</b>")
    return "\n".join(lines)


def discover_kb(user_id: int, tenant_id: int) -> InlineKeyboardMarkup:
    def b(text: str, a: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=Dt(a=a, u=user_id, t=tenant_id).pack())

    return InlineKeyboardMarkup(inline_keyboard=[
        [b("❤️ Like", "lk"), b("👎 Pass", "ps")],
        [b("🔥 Crush", "cr"), b("🚫 Block", "bl"), b("🚨 Report", "rp")],
    ])


# --- couples (pure) ------------------------------------------------------------------------------------------------

def couple_level(xp: int) -> int:
    return math.isqrt(max(xp, 0) // 50)


def couple_achievements(c: Couple, now: datetime) -> list[tuple[str, bool]]:
    days = (now - aware(c.since)).days
    level = couple_level(c.xp)
    return [
        ("💞 One week together", days >= 7),
        ("🌙 One month together", days >= 30),
        ("💯 100 days together", days >= 100),
        ("🎂 One year together", days >= 365),
        ("🍷 Sweethearts (200 couple XP)", c.xp >= 200),
        ("🔥 Power couple (1,000 couple XP)", c.xp >= 1000),
        ("⭐ Couple level 5", level >= 5),
    ]
