from __future__ import annotations

from html import escape

from app.bot import ui
from app.bot.context import Ctx
from app.bot.registry import command
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import platform
from app.services.permissions import can_manage, resolve_role
from app.services.roles import GLOBAL_ASSIGNABLE, Role, parse_role
from app.utils.errors import RECENT_ERRORS

CAT = "owner"


@command("owner", description="Open the owner panel", category=CAT, permission=Role.OWNER, scope="private")
async def owner(ctx: Ctx) -> None:
    text, kb = await ui.owner_view(ctx.session, ctx.cache)
    await ctx.reply(text, kb)


@command("stats", description="Platform statistics", category=CAT, permission=Role.DEVELOPER, scope="private")
async def stats(ctx: Ctx) -> None:
    await ctx.reply(await platform.stats_text(ctx.session))


@command("health", description="Database, Redis and Telegram health", category=CAT,
         permission=Role.DEVELOPER, scope="private")
async def health(ctx: Ctx) -> None:
    await ctx.reply(await platform.health_text(ctx.session, ctx.cache, ctx.bot, ctx.settings))


@command("users", description="Recently active users", category=CAT, permission=Role.SUPER_ADMIN, scope="private")
async def users(ctx: Ctx) -> None:
    total = await users_repo.count_users(ctx.session)
    rows = await users_repo.recent_users(ctx.session, 10)
    lines = [f"<b>👥 Users</b> · {total} total", ""]
    for u in rows:
        name = escape(u.first_name or "—")
        handle = f" @{escape(u.username)}" if u.username else ""
        lines.append(f"• <code>{u.id}</code> {name}{handle}{' ⛔' if u.is_globally_banned else ''}")
    await ctx.reply("\n".join(lines))


@command("groups", description="Chats the bot serves (paginated)", category=CAT,
         permission=Role.SUPER_ADMIN, scope="private")
async def groups(ctx: Ctx) -> None:
    text, kb = await ui.owner_groups_view(ctx.session, 0)
    await ctx.reply(text, kb)


@command("channels", description="Channels the bot serves", category=CAT,
         permission=Role.SUPER_ADMIN, scope="private")
async def channels(ctx: Ctx) -> None:
    n = await tenants_repo.count_tenants(ctx.session, ("channel",))
    await ctx.reply(f"📢 Channels registered: <b>{n}</b>. Use /groups to see every chat.")


@command("maintenance", description="Turn maintenance mode on/off (asks to confirm)", category=CAT,
         permission=Role.OWNER, scope="private")
async def maintenance(ctx: Ctx) -> None:
    current = await platform.is_maintenance(ctx.session, ctx.cache)
    text, kb = ui.confirm_maintenance_view(current)
    await ctx.reply(text, kb)


async def _ban(ctx: Ctx, banned: bool) -> None:
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply(f"Usage: <code>/{ctx.name} &lt;id|@user&gt;</code> or reply to a message.")
        return
    current = await resolve_role(ctx.session, ctx.bot, ctx.cache, ctx.settings, target.id, None)
    if banned and not can_manage(ctx.role, current):
        await ctx.reply("🚫 You can't ban someone with an equal or higher role.")
        return
    user = await users_repo.set_global_ban(ctx.session, target.id, banned)
    if user is None:
        await ctx.reply("I haven't seen that user yet, so there is nothing to change.")
        return
    await tenants_repo.add_audit(ctx.session, "global_ban" if banned else "global_unban",
                                 ctx.user_id, None, {"target": target.id})
    await ctx.reply(f"{'⛔ Globally banned' if banned else '✅ Unbanned'} <code>{target.id}</code>.")


@command("globalban", description="Ban a user from using the bot anywhere", category=CAT,
         usage="/globalban <id|@user|reply>", permission=Role.SUPER_ADMIN)
async def globalban(ctx: Ctx) -> None:
    await _ban(ctx, True)


@command("globalunban", description="Lift a global ban", category=CAT,
         usage="/globalunban <id|@user|reply>", permission=Role.SUPER_ADMIN)
async def globalunban(ctx: Ctx) -> None:
    await _ban(ctx, False)


@command("grantrole", description="Give a platform-wide role (developer / superadmin)", category=CAT,
         usage="/grantrole <id|@user> <developer|superadmin>", permission=Role.OWNER, scope="private")
async def grantrole(ctx: Ctx) -> None:
    target, rest = await ctx.resolve_target()
    role = parse_role(rest[0], GLOBAL_ASSIGNABLE) if rest else None
    if target is None or role is None:
        await ctx.reply("Usage: <code>/grantrole 12345 developer</code>")
        return
    await users_repo.set_global_role(ctx.session, target.id, int(role), ctx.user_id)
    await tenants_repo.add_audit(ctx.session, "global_role_granted", ctx.user_id, None,
                                 {"target": target.id, "role": role.name})
    await ctx.reply(f"✅ <code>{target.id}</code> is now <b>{role.label}</b>.")


@command("revokerole", description="Remove a platform-wide role", category=CAT,
         usage="/revokerole <id|@user>", permission=Role.OWNER, scope="private")
async def revokerole(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/revokerole 12345</code>")
        return
    removed = await users_repo.remove_global_role(ctx.session, target.id)
    if removed:
        await tenants_repo.add_audit(ctx.session, "global_role_revoked", ctx.user_id, None, {"target": target.id})
    await ctx.reply("✅ Role removed." if removed else "That user has no platform role.")


@command("config", description="Show non-secret configuration status", category=CAT,
         permission=Role.OWNER, scope="private")
async def config(ctx: Ctx) -> None:
    s = ctx.settings
    yes = lambda v: "✅" if v else "—"  # noqa: E731
    await ctx.reply(
        "<b>⚙️ Configuration</b>\n"
        f"Environment: <code>{escape(s.environment)}</code>\n"
        f"Mode: <code>{'webhook' if s.use_webhook else 'polling'}</code>\n"
        f"Log level: <code>{escape(s.log_level)}</code>\n"
        f"Redis connected: {yes(ctx.cache.redis_ok)}\n"
        f"Owner ID set: {yes(s.owner_id)}\n"
        f"Gemini key set: {yes(s.gemini_api_key)} · OpenAI key set: {yes(s.openai_api_key)}\n"
        "<i>Secrets are never displayed.</i>"
    )


@command("errors", description="Most recent errors since the last restart", category=CAT,
         permission=Role.DEVELOPER, scope="private")
async def errors(ctx: Ctx) -> None:
    if not RECENT_ERRORS:
        await ctx.reply("✅ No errors recorded since the last restart.")
        return
    lines = ["<b>🚨 Recent errors</b>"]
    for e in list(RECENT_ERRORS)[-10:][::-1]:
        lines.append(f"{e.at:%H:%M:%S} · {escape(e.where)} · <code>{escape(e.summary)}</code>")
    await ctx.reply("\n".join(lines))


@command("logs", description="Recent audit log entries (all chats)", category=CAT,
         permission=Role.DEVELOPER, scope="private")
async def logs(ctx: Ctx) -> None:
    rows = await tenants_repo.recent_audit(ctx.session, None, 15)
    if not rows:
        await ctx.reply("Nothing logged yet.")
        return
    lines = ["<b>📜 Audit log</b>"]
    for r in rows:
        lines.append(f"{r.created_at:%m-%d %H:%M} · <code>{escape(r.action)}</code> · actor <code>{r.actor_id}</code> · tenant {r.tenant_id or '-'}")
    await ctx.reply("\n".join(lines))
