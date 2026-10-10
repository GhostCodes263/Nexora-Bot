from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.registry import CommandSpec
from app.config.settings import Settings
from app.database.models import Tenant, User
from app.repositories import users as users_repo
from app.services.cache import Cache
from app.services.roles import Role


@dataclass
class Target:
    id: int
    name: str


@dataclass
class Ctx:
    bot: Bot
    message: Message
    session: AsyncSession
    cache: Cache
    settings: Settings
    spec: CommandSpec
    name: str
    raw_args: str
    role: Role
    user: User
    tenant: Tenant | None
    anonymous: bool = False
    state: Any = None  # aiogram FSMContext (used by multi-step flows such as /verify)

    @property
    def args(self) -> list[str]:
        return self.raw_args.split()

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def in_group(self) -> bool:
        return self.tenant is not None

    async def reply(self, text: str, kb: InlineKeyboardMarkup | None = None) -> Message:
        return await self.message.reply(text, reply_markup=kb)

    async def resolve_target(self) -> tuple[Target | None, list[str]]:
        """Find the user a command targets: reply > numeric ID > @username seen by the bot.

        Returns (target, remaining_args). Telegram IDs are only trusted as identifiers;
        permissions are always re-checked server-side.
        """
        m = self.message
        reply = m.reply_to_message
        if reply and reply.from_user and not reply.from_user.is_bot:
            u = reply.from_user
            await users_repo.upsert_user(self.session, u.id, u.username, u.first_name or "")
            return Target(u.id, u.full_name), self.args
        args = self.args
        if not args:
            return None, []
        first = args[0]
        if first.isdigit():
            known = await users_repo.get_user(self.session, int(first))
            return Target(int(first), (known.first_name if known else "") or first), args[1:]
        if first.startswith("@"):
            found = await users_repo.find_by_username(self.session, first)
            if found:
                return Target(found.id, found.first_name or first), args[1:]
        return None, args
