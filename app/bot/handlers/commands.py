from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.context import Ctx
from app.bot.parse import parse_command
from app.bot.registry import REGISTRY
from app.config.settings import Settings
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import platform, plans
from app.services import tenants as tenant_service
from app.services.cache import Cache
from app.services.permissions import ANONYMOUS_ADMIN_ID, resolve_role
from app.services.roles import Role
from app.utils.telegram import safe_reply

log = logging.getLogger(__name__)
router = Router(name="commands")

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


@router.message(F.text)
async def on_text(
    message: Message, bot: Bot, session: AsyncSession, cache: Cache, settings: Settings, state: FSMContext
) -> object:
    text = message.text or ""
    in_group = message.chat.type in GROUP_TYPES
    first = text[:1]
    # Cheap exit for ordinary chatter: only "/" or a symbol (custom prefix) can start a command.
    if first != "/" and (first.isalnum() or first.isspace() or not in_group):
        return UNHANDLED
    if message.from_user is None:
        return UNHANDLED

    anonymous = bool(message.sender_chat and message.sender_chat.id == message.chat.id)
    if message.sender_chat and not anonymous:
        return UNHANDLED  # messages sent on behalf of another channel are not commands

    tenant = None
    if in_group:
        tenant = await tenants_repo.get_or_create(
            session, message.chat.id, message.chat.type, message.chat.title or ""
        )
        if not tenant.is_active:
            return UNHANDLED

    me = await bot.me()
    parsed = parse_command(text, me.username or "", tenant.prefix if tenant else "/")
    if parsed is None:
        return UNHANDLED
    name, raw_args = parsed
    spec = REGISTRY.get(name)
    if spec is None and tenant is None:
        return UNHANDLED

    fu = message.from_user
    user_id = ANONYMOUS_ADMIN_ID if anonymous else fu.id
    if anonymous:
        user = await users_repo.get_user(session, user_id) or await users_repo.upsert_user(
            session, user_id, None, "Anonymous admin"
        )
    else:
        user = await users_repo.upsert_user(session, fu.id, fu.username, fu.full_name)

    role = await resolve_role(session, bot, cache, settings, user_id, tenant, anonymous_admin=anonymous)

    if user.is_globally_banned and role < Role.DEVELOPER:
        return None

    if spec is None:
        return await _custom_command(message, session, cache, tenant, name, user_id, role, settings)

    # Hide the existence of staff-only commands from everyone else.
    if spec.permission > role:
        if spec.permission < Role.SUPER_ADMIN:
            await message.reply(f"🚫 You need the <b>{spec.permission.label}</b> role to use /{spec.name}.")
        return None

    if role < Role.OWNER and await cache.hit(f"rl:{user_id}", 10) > 8:
        if await cache.set_nx(f"rlwarn:{user_id}", "1", 10):
            await message.reply("⏳ Slow down a little.")
        return None

    if role < Role.DEVELOPER and await platform.is_maintenance(session, cache):
        await message.reply("🛠 The bot is under maintenance. Please try again soon.")
        return None

    if spec.scope == "group" and tenant is None:
        await message.reply("This command only works inside a group.")
        return None
    if spec.scope == "private" and tenant is not None:
        await message.reply("Please use this command in a private chat with me.")
        return None

    if not tenant_service.is_enabled(tenant, spec):
        await message.reply(
            f"🚫 The <b>{spec.category}</b> module is disabled here. "
            f"An admin can enable it with <code>/enable {spec.category}</code>."
        )
        return None

    if spec.cooldown and role < Role.MODERATOR:
        scope = tenant.id if tenant else 0
        wait = await cache.cooldown(f"cd:{scope}:{user_id}:{spec.name}", spec.cooldown)
        if wait:
            await message.reply(f"⏳ Try /{spec.name} again in {wait}s.")
            return None

    ctx = Ctx(
        bot=bot, message=message, session=session, cache=cache, settings=settings, spec=spec,
        name=name, raw_args=raw_args, role=role, user=user, tenant=tenant, anonymous=anonymous,
        state=state,
    )
    await spec.handler(ctx)
    return None


async def _custom_command(
    message: Message, session: AsyncSession, cache: Cache, tenant, name: str, user_id: int, role: Role,
    settings: Settings,
) -> object:
    """Per-group custom commands (a paid feature). Unknown commands stay silent otherwise."""
    from sqlalchemy import select

    from app.database.models import CustomCommand

    if tenant is None:
        return UNHANDLED
    plan = await plans.effective_view(session, cache, tenant)
    if not plan.has("custom_commands"):
        return UNHANDLED
    row = (await session.execute(select(CustomCommand).where(
        CustomCommand.tenant_id == tenant.id, CustomCommand.name == name))).scalars().first()
    if row is None:
        return UNHANDLED
    if role < Role.MODERATOR and await cache.cooldown(f"cc:{tenant.id}:{user_id}:{name}", 3):
        return None
    await safe_reply(message, row.response)
    return None
