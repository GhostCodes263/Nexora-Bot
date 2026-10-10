from __future__ import annotations

import math
from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import categories
from app.bot.registry import REGISTRY, CommandSpec
from app.database.models import Tenant
from app.repositories import tenants as tenants_repo
from app.services import platform
from app.services import tenants as tenant_service
from app.services.cache import Cache
from app.services.roles import Role

PAGE = 8
View = tuple[str, InlineKeyboardMarkup]


class Nav(CallbackData, prefix="nav"):
    a: str
    p: str = ""
    n: int = 0


def _kb(rows: list[list[tuple[str, Nav]]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t, callback_data=cd.pack()) for t, cd in row])
    return b.as_markup()


HOME = ("🏠 Home", Nav(a="home"))
CLOSE = ("❌ Close", Nav(a="close"))


def visible(role: Role, tenant: Tenant | None, in_group: bool) -> list[CommandSpec]:
    out = []
    for s in REGISTRY.all():
        if s.hidden or s.permission > role:
            continue
        if s.scope == "group" and not in_group:
            continue
        if s.scope == "private" and in_group:
            continue
        if not tenant_service.is_enabled(tenant, s):
            continue
        out.append(s)
    return out


def home_view(role: Role, tenant: Tenant | None, in_group: bool) -> View:
    specs = visible(role, tenant, in_group)
    cats = sorted({s.category for s in specs}, key=categories.order_key)
    rows: list[list[tuple[str, Nav]]] = []
    buf: list[tuple[str, Nav]] = []
    for c in cats:
        emoji, title, _ = categories.info(c)
        buf.append((f"{emoji} {title}", Nav(a="cat", p=c)))
        if len(buf) == 2:
            rows.append(buf)
            buf = []
    if buf:
        rows.append(buf)
    extra = []
    if in_group and role >= Role.ADMIN:
        extra.append(("⚙️ Settings", Nav(a="settings")))
    if not in_group and role >= Role.OWNER:
        extra.append(("👑 Owner panel", Nav(a="owner")))
    if extra:
        rows.append(extra)
    rows.append([("🔎 Search help", Nav(a="searchhelp")), CLOSE])
    text = (
        "<b>🤖 Main menu</b>\n\nPick a category below.\n"
        "Tip: <code>/help ban</code> searches every command."
    )
    return text, _kb(rows)


def search_help_view() -> View:
    return (
        "<b>🔎 Search</b>\nSend <code>/help &lt;word&gt;</code>, for example "
        "<code>/help time</code>, to find commands by name or description.",
        _kb([[HOME]]),
    )


def category_view(category: str, page: int, role: Role, tenant: Tenant | None, in_group: bool) -> View:
    emoji, title, _ = categories.info(category)
    specs = [s for s in visible(role, tenant, in_group) if s.category == category]
    if not specs:
        return f"<b>{emoji} {title}</b>\nNothing available here.", _kb([[HOME]])
    pages = max(1, math.ceil(len(specs) / PAGE))
    page = max(0, min(page, pages - 1))
    chunk = specs[page * PAGE:(page + 1) * PAGE]
    rows = [[(f"/{s.name} · {s.description[:26]}", Nav(a="cmd", p=s.name))] for s in chunk]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️", Nav(a="cat", p=category, n=page - 1)))
        nav.append((f"{page + 1}/{pages}", Nav(a="noop")))
        if page < pages - 1:
            nav.append(("▶️", Nav(a="cat", p=category, n=page + 1)))
        rows.append(nav)
    rows.append([("⬅️ Back", Nav(a="home")), HOME])
    return f"<b>{emoji} {title}</b> · {len(specs)} commands", _kb(rows)


def command_view(name: str, role: Role, tenant: Tenant | None, in_group: bool) -> View:
    spec = REGISTRY.get(name)
    allowed = {s.name for s in visible(role, tenant, in_group)}
    if spec is None or spec.name not in allowed:
        return "Command not found.", _kb([[HOME]])
    lines = [f"<b>/{spec.name}</b> — {escape(spec.description)}", ""]
    lines.append(f"<b>Usage:</b> <code>{escape(spec.usage)}</code>")
    if spec.aliases:
        lines.append("<b>Aliases:</b> " + ", ".join(f"/{a}" for a in spec.aliases))
    lines.append(f"<b>Permission:</b> {spec.permission.label}")
    if spec.cooldown:
        lines.append(f"<b>Cooldown:</b> {spec.cooldown}s")
    if spec.scope != "any":
        lines.append(f"<b>Works in:</b> {'groups' if spec.scope == 'group' else 'private chat'}")
    if spec.examples:
        lines.append("<b>Examples:</b>")
        lines.extend(f"<code>{escape(e)}</code>" for e in spec.examples)
    rows = [[("⬅️ Back", Nav(a="cat", p=spec.category)), HOME]]
    return "\n".join(lines), _kb(rows)


def search_view(query: str, role: Role, tenant: Tenant | None, in_group: bool) -> View:
    allowed = {s.name for s in visible(role, tenant, in_group)}
    hits = [s for s in REGISTRY.search(query) if s.name in allowed][:PAGE]
    if not hits:
        return f"No commands match <b>{escape(query)}</b>.", _kb([[HOME]])
    rows = [[(f"/{s.name} · {s.description[:26]}", Nav(a="cmd", p=s.name))] for s in hits]
    rows.append([HOME])
    return f"<b>🔎 Results for “{escape(query)}”</b>", _kb(rows)


def settings_view(tenant: Tenant) -> View:
    rows = []
    for key in categories.toggleable():
        emoji, title, _ = categories.info(key)
        on = tenant_service.module_enabled(tenant, key)
        rows.append([(f"{'✅' if on else '❌'} {emoji} {title}", Nav(a="toggle", p=key))])
    rows.append([HOME, CLOSE])
    text = (
        f"<b>⚙️ Settings</b> · {escape(tenant.title or 'this chat')}\n\n"
        f"Command prefix: <code>{escape(tenant.prefix)}</code>  (change with <code>/prefix</code>)\n"
        "Tap a module to switch it on or off for this group."
    )
    return text, _kb(rows)


async def owner_view(session: AsyncSession, cache: Cache) -> View:
    maint = await platform.is_maintenance(session, cache)
    rows = [
        [("📊 Stats", Nav(a="ostats")), ("🏠 Groups", Nav(a="ogroups"))],
        [(f"🛠 Maintenance: {'ON' if maint else 'OFF'}", Nav(a="maint", p="ask"))],
        [HOME, CLOSE],
    ]
    return "<b>👑 Owner panel</b>\nPlatform controls.", _kb(rows)


async def owner_groups_view(session: AsyncSession, page: int) -> View:
    total = await tenants_repo.count_tenants(session)
    pages = max(1, math.ceil(total / PAGE))
    page = max(0, min(page, pages - 1))
    items = await tenants_repo.list_tenants(session, page * PAGE, PAGE)
    lines = [f"<b>🏠 Chats</b> · {total} total (page {page + 1}/{pages})", ""]
    for t in items:
        state = "🟢" if t.is_active else "⚫️"
        lines.append(f"{state} <code>{t.chat_id}</code> {escape(t.title or '—')} ({t.chat_type})")
    if not items:
        lines.append("No chats yet.")
    nav = []
    if page > 0:
        nav.append(("◀️", Nav(a="ogroups", n=page - 1)))
    if page < pages - 1:
        nav.append(("▶️", Nav(a="ogroups", n=page + 1)))
    rows = [nav] if nav else []
    rows.append([("⬅️ Back", Nav(a="owner")), HOME])
    return "\n".join(lines), _kb(rows)


def confirm_maintenance_view(currently_on: bool) -> View:
    action = "turn OFF" if currently_on else "turn ON"
    text = f"<b>⚠️ Confirm</b>\nDo you really want to {action} maintenance mode?\nOnly staff can use the bot while it is on."
    return text, _kb([[("✅ Yes", Nav(a="maint", p="yes")), ("✖️ Cancel", Nav(a="owner"))]])


def confirm_forget_view() -> View:
    text = (
        "<b>⚠️ Delete my data</b>\nThis clears your stored name/username and removes any "
        "roles you hold in groups. It cannot be undone."
    )
    return text, _kb([[("🗑 Delete", Nav(a="forget", p="yes")), ("✖️ Cancel", Nav(a="close"))]])
