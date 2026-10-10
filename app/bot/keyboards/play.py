from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class Gm(CallbackData, prefix="gm"):
    """Generic game button: g=game code, id=game instance, a=action, n=number."""

    g: str
    id: str
    a: str = ""
    n: int = 0


class Trade(CallbackData, prefix="tr"):
    a: str  # yes | no
    id: str


def rows(*lines: list[tuple[str, CallbackData]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=cd.pack()) for t, cd in line] for line in lines
    ])
