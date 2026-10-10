from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo

log = logging.getLogger(__name__)
router = Router(name="chat_events")

_PRESENT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
_GONE = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

WELCOME = (
    "👋 <b>Hi! I'm ready.</b>\n"
    "For full functionality, make me an admin of this chat.\n"
    "Send /menu for the interactive menu or /help to browse commands."
)


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, session: AsyncSession) -> None:
    chat = event.chat
    if chat.type == ChatType.PRIVATE:
        return
    new, old = event.new_chat_member.status, event.old_chat_member.status
    actor = event.from_user
    if new in _PRESENT:
        await users_repo.upsert_user(session, actor.id, actor.username, actor.full_name)
        tenant = await tenants_repo.get_or_create(
            session, chat.id, chat.type, chat.title or "", added_by=actor.id
        )
        tenant.is_active = True
        await tenants_repo.add_audit(session, "bot_added", actor.id, tenant.id, {"chat": chat.id})
        if old in _GONE:
            try:
                await event.bot.send_message(chat.id, WELCOME)  # type: ignore[union-attr]
            except TelegramAPIError as exc:
                log.info("welcome not sent: %s", exc)
    elif new in _GONE:
        tenant = await tenants_repo.get_by_chat_id(session, chat.id)
        if tenant is not None:
            # Keep data: a customer who re-adds the bot (or renews later) gets everything back.
            tenant.is_active = False
            await tenants_repo.add_audit(session, "bot_removed", actor.id, tenant.id, {})
