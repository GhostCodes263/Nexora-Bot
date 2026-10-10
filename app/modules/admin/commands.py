from __future__ import annotations

from html import escape

from app.bot import categories
from app.bot.context import Ctx
from app.bot.registry import command
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import tenants as tenant_service
from app.services.permissions import can_manage, resolve_role
from app.services.roles import TENANT_ASSIGNABLE, Role, parse_role

CAT = "admin"


@command("myrole", description="Show your role in this chat", category=CAT, scope="group")
async def myrole(ctx: Ctx) -> None:
    await ctx.reply(f"🎖 Your role here: <b>{ctx.role.label}</b>")


@command("prefix", description="Change this group's command prefix", category=CAT,
         usage="/prefix <symbol>", examples=("/prefix !", "/prefix /"),
         permission=Role.ADMIN, scope="group")
async def prefix(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    new = ctx.raw_args.strip()
    if not new:
        await ctx.reply(f"Current prefix: <code>{escape(ctx.tenant.prefix)}</code>\nChange it with <code>/prefix !</code>")
        return
    if len(new) > 2 or any(c.isalnum() or c.isspace() or c in "@<>&" for c in new):
        await ctx.reply("The prefix must be 1-2 symbols such as <code>!</code> or <code>.</code>")
        return
    tenants_repo.set_prefix(ctx.tenant, new)
    await tenants_repo.add_audit(ctx.session, "prefix_changed", ctx.user_id, ctx.tenant.id, {"prefix": new})
    await ctx.reply(f"✅ Prefix set to <code>{escape(new)}</code>. <code>/</code> always keeps working.")


@command("modules", description="List modules and whether they are enabled", category=CAT,
         permission=Role.ADMIN, scope="group")
async def modules(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    lines = ["<b>🧩 Modules</b>"]
    for key in categories.toggleable():
        emoji, title, _ = categories.info(key)
        on = tenant_service.module_enabled(ctx.tenant, key)
        lines.append(f"{'✅' if on else '❌'} {emoji} {title} (<code>{key}</code>)")
    lines.append("\nToggle with <code>/enable &lt;module&gt;</code> or <code>/disable &lt;module&gt;</code>.")
    await ctx.reply("\n".join(lines))


async def _toggle(ctx: Ctx, enabled: bool) -> None:
    assert ctx.tenant is not None
    key = ctx.raw_args.strip().lower()
    if key not in categories.toggleable():
        await ctx.reply("Unknown module. Options: " + ", ".join(f"<code>{k}</code>" for k in categories.toggleable()))
        return
    tenants_repo.set_module(ctx.tenant, key, enabled)
    await tenants_repo.add_audit(ctx.session, "module_toggled", ctx.user_id, ctx.tenant.id,
                                 {"module": key, "enabled": enabled})
    await ctx.reply(f"{'✅ Enabled' if enabled else '🚫 Disabled'} <b>{key}</b>.")


@command("enable", description="Enable a module for this group", category=CAT,
         usage="/enable <module>", examples=("/enable utility",), permission=Role.ADMIN, scope="group")
async def enable(ctx: Ctx) -> None:
    await _toggle(ctx, True)


@command("disable", description="Disable a module for this group", category=CAT,
         usage="/disable <module>", examples=("/disable utility",), permission=Role.ADMIN, scope="group")
async def disable(ctx: Ctx) -> None:
    await _toggle(ctx, False)


@command("promote", description="Give someone a staff role in this group", category=CAT,
         usage="/promote <reply|id|@user> <trusted|moderator|admin>",
         examples=("/promote @alex moderator",), permission=Role.ADMIN, scope="group")
async def promote(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, rest = await ctx.resolve_target()
    new_role = parse_role(rest[0], TENANT_ASSIGNABLE) if rest else None
    if target is None or new_role is None:
        await ctx.reply("Usage: <code>/promote @user moderator</code> (roles: trusted, moderator, admin)")
        return
    current = await resolve_role(ctx.session, ctx.bot, ctx.cache, ctx.settings, target.id, ctx.tenant)
    if not can_manage(ctx.role, current, new_role):
        await ctx.reply("🚫 You can only manage people below your own role, and grant roles below it.")
        return
    await tenants_repo.set_role(ctx.session, ctx.tenant.id, target.id, int(new_role), ctx.user_id)
    await tenants_repo.add_audit(ctx.session, "role_granted", ctx.user_id, ctx.tenant.id,
                                 {"target": target.id, "role": new_role.name})
    await ctx.cache.delete(f"tgrole:{ctx.tenant.chat_id}:{target.id}")
    await ctx.reply(f"✅ {escape(target.name)} is now <b>{new_role.label}</b>.")


@command("demote", description="Remove someone's staff role in this group", category=CAT,
         usage="/demote <reply|id|@user>", permission=Role.ADMIN, scope="group")
async def demote(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/demote @user</code> (or reply to their message)")
        return
    stored = await tenants_repo.get_role(ctx.session, ctx.tenant.id, target.id)
    if stored is None:
        await ctx.reply("They have no role assigned by this bot in this group.")
        return
    if not can_manage(ctx.role, Role(stored)):
        await ctx.reply("🚫 You can only manage people below your own role.")
        return
    await tenants_repo.remove_role(ctx.session, ctx.tenant.id, target.id)
    await tenants_repo.add_audit(ctx.session, "role_removed", ctx.user_id, ctx.tenant.id, {"target": target.id})
    await ctx.cache.delete(f"tgrole:{ctx.tenant.chat_id}:{target.id}")
    await ctx.reply(f"✅ Removed the role of {escape(target.name)}.")


@command("staff", description="List roles assigned in this group", category=CAT,
         permission=Role.MODERATOR, scope="group")
async def staff(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rows = await tenants_repo.list_roles(ctx.session, ctx.tenant.id)
    if not rows:
        await ctx.reply("No roles assigned yet. Telegram admins are recognised automatically.")
        return
    lines = ["<b>👮 Staff</b>"]
    for r in rows:
        u = await users_repo.get_user(ctx.session, r.user_id)
        who = escape(u.first_name) if u and u.first_name else str(r.user_id)
        lines.append(f"• {who} (<code>{r.user_id}</code>) — {Role(r.role).label}")
    await ctx.reply("\n".join(lines))


@command("auditlog", description="Recent admin actions in this group", category=CAT,
         aliases=("audit",), permission=Role.ADMIN, scope="group")
async def auditlog(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rows = await tenants_repo.recent_audit(ctx.session, ctx.tenant.id, 10)
    if not rows:
        await ctx.reply("Nothing logged yet.")
        return
    lines = ["<b>📜 Audit log</b>"]
    for r in rows:
        lines.append(f"{r.created_at:%m-%d %H:%M} · <code>{escape(r.action)}</code> by <code>{r.actor_id}</code>")
    await ctx.reply("\n".join(lines))
