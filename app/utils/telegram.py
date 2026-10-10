from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message


async def safe_reply(message: Message, text: str, **kwargs) -> Message:
    """Reply with HTML; if admin-written text has broken markup, resend it as plain text."""
    try:
        return await message.reply(text, **kwargs)
    except TelegramBadRequest:
        return await message.reply(text, parse_mode=None, **kwargs)
