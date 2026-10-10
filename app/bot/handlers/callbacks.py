from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import categories, ui
from app.bot.ui import Nav
from app.config.settings import Settings
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import platform
from app.services import tenants as tenant_service
from app.services.cache import Cache
from app.services.permissions import resolve_role
from app.services.roles import Role

log = logging.getLogger(__name__)
router = Router(name="callbacks")
GROUP_TYPES = {"group", "supergroup"}


async def _show(cb: CallbackQuery, view: ui.View) -> None:
    text, kb = view
    try:
        await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


@router.callback_query(Nav.filter())
async def on_nav(
    cb: CallbackQuery, callback_data: Nav, bot: Bot, session: AsyncSession, cache: Cache,
    settings: Settings,
) -> None:
    msg = cb.message
    if msg is None or not hasattr(msg, "chat"):
        await cb.answer("This menu has expired. Send /menu again.", show_alert=True)
        return
    uid = cb.from_user.id
    if await cache.hit(f"rlcb:{uid}", 5) > 12:
        await cb.answer("Slow down 🙂")
        return

    chat = msg.chat  # type: ignore[union-attr]
    in_group = chat.type in GROUP_TYPES
    tenant = await tenants_repo.get_by_chat_id(session, chat.id) if in_group else None
    user = await users_repo.upsert_user(session, uid, cb.from_user.username, cb.from_user.full_name)
    if user.is_globally_banned and uid != settings.owner_id:
        await cb.answer()
        return
    # Role of the person who PRESSED the button, never of whoever opened the menu.
    role = await resolve_role(session, bot, cache, settings, uid, tenant)
    a, p, n = callback_data.a, callback_data.p, callback_data.n

    if a == "noop":
        await cb.answer()
    elif a == "close":
        await cb.answer()
        try:
            await msg.delete()  # type: ignore[union-attr]
        except TelegramBadRequest:
            pass
    elif a == "home":
        await _show(cb, ui.home_view(role, tenant, in_group))
        await cb.answer()
    elif a == "searchhelp":
        await _show(cb, ui.search_help_view())
        await cb.answer()
    elif a == "cat":
        await _show(cb, ui.category_view(p, n, role, tenant, in_group))
        await cb.answer()
    elif a == "cmd":
        await _show(cb, ui.command_view(p, role, tenant, in_group))
        await cb.answer()
    elif a in {"settings", "toggle"}:
        if tenant is None or role < Role.ADMIN:
            await cb.answer("Admins only.", show_alert=True)
            return
        if a == "toggle":
            if p not in categories.toggleable():
                await cb.answer("That module can't be toggled.", show_alert=True)
                return
            new_state = not tenant_service.module_enabled(tenant, p)
            tenants_repo.set_module(tenant, p, new_state)
            await tenants_repo.add_audit(
                session, "module_toggled", uid, tenant.id, {"module": p, "enabled": new_state}
            )
        await _show(cb, ui.settings_view(tenant))
        await cb.answer()
    elif a in {"owner", "ostats", "ogroups", "maint"}:
        if role < Role.OWNER or in_group:
            await cb.answer("Not allowed.", show_alert=True)
            return
        if a == "owner":
            await _show(cb, await ui.owner_view(session, cache))
        elif a == "ostats":
            _, kb = await ui.owner_view(session, cache)
            await _show(cb, (await platform.stats_text(session), kb))
        elif a == "ogroups":
            await _show(cb, await ui.owner_groups_view(session, n))
        elif a == "maint":
            current = await platform.is_maintenance(session, cache)
            if p == "yes":
                await platform.set_maintenance(session, cache, not current)
                await tenants_repo.add_audit(session, "maintenance", uid, None, {"on": not current})
                await _show(cb, await ui.owner_view(session, cache))
            else:
                await _show(cb, ui.confirm_maintenance_view(current))
        await cb.answer()
    elif a == "forget":
        if p == "yes":
            await _forget(session, uid)
            await _show(cb, ("✅ Your stored data has been cleared.", ui._kb([])))
        else:
            await _show(cb, ui.confirm_forget_view())
        await cb.answer()
    else:
        await cb.answer()


async def _forget(session: AsyncSession, user_id: int) -> None:
    """Anonymise the caller's own record. Only ever touches the pressing user."""
    from sqlalchemy import delete

    from app.database.models import TenantRole, VerificationApplication

    user = await users_repo.get_user(session, user_id)
    if user is not None:
        user.username = None
        user.first_name = ""
    await session.execute(delete(TenantRole).where(TenantRole.user_id == user_id))
    await session.execute(delete(VerificationApplication).where(VerificationApplication.user_id == user_id))
    from app.repositories import dating as dating_repo

    await dating_repo.delete_all(session, user_id)
    await tenants_repo.add_audit(session, "user_data_cleared", user_id, None, {})
