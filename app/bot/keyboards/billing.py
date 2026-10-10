from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.models import Plan
from app.services.plans import FEATURE_LABELS, LIMIT_LABELS, money

View = tuple[str, InlineKeyboardMarkup]


class Bill(CallbackData, prefix="bill"):
    a: str  # plans | pick | buy | trial
    p: str = ""  # plan code
    t: str = ""  # period: m | y


def _kb(rows: list[list[tuple[str, Bill]]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t, callback_data=cd.pack()) for t, cd in row])
    return b.as_markup()


def plans_button() -> InlineKeyboardMarkup:
    return _kb([[("💎 View plans", Bill(a="plans"))]])


def plans_view(plans: list[Plan], current: str, trial_days: int) -> View:
    lines = ["<b>💎 Plans</b>", ""]
    rows: list[list[tuple[str, Bill]]] = []
    for p in plans:
        mark = " ← current" if p.code == current else ""
        if p.price_month_cents <= 0:
            lines.append(f"• <b>{p.name}</b> — free{mark}")
            continue
        lines.append(f"• <b>{p.name}</b> — {money(p.price_month_cents)}/mo or {money(p.price_year_cents)}/yr{mark}")
        rows.append([(f"⭐ {p.name} · {money(p.price_month_cents)}/mo", Bill(a="pick", p=p.code))])
    lines.append(f"\nPaid plans include a {trial_days}-day free trial. Pay with Telegram Stars.")
    return "\n".join(lines), _kb(rows)


def pick_view(plan: Plan, trial_days: int) -> View:
    lines = [f"<b>⭐ {plan.name}</b>", ""]
    lines += [f"✅ {FEATURE_LABELS.get(f, f)}" for f in plan.features or []]
    lims = plan.limits or {}
    lines.append("")
    lines += [f"• up to {v} {LIMIT_LABELS.get(k, k)}" for k, v in lims.items()]
    rows = [
        [(f"Monthly · {plan.stars_month} ⭐ ({money(plan.price_month_cents)})", Bill(a="buy", p=plan.code, t="m"))],
        [(f"Yearly · {plan.stars_year} ⭐ ({money(plan.price_year_cents)})", Bill(a="buy", p=plan.code, t="y"))],
        [(f"🎁 {trial_days}-day free trial", Bill(a="trial", p=plan.code))],
        [("⬅️ Plans", Bill(a="plans"))],
    ]
    return "\n".join(lines), _kb(rows)
