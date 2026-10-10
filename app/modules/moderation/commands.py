from __future__ import annotations

from datetime import timedelta
from html import escape

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import delete, func, select

from app.bot.context import Ctx, Target
from app.bot.registry import command
from app.database.models import ModAction, ModNote, ModWarning
from app.modules.moderation import service as svc
from app.repositories import users as users_repo
from app.services.gates import plan_of, require_feature
from app.services.permissions import can_manage, resolve_role
from app.services.roles import Role
from app.utils.time import utcnow

CAT = "moderation"


# --- helpers -----------------------------------------------------------------------------------

async def _guard(ctx: Ctx, usage: str) -> tuple[Target, list[str]] | None:
    """Resolve the target and make sure the actor outranks them. Replies and returns None if not."""
    assert ctx.tenant is not None
    target, rest = await ctx.resolve_target()
    if target is None:
        await ctx.reply(f"Usage: <code>{usage}</code> (reply to a message, or use an ID/@username)")
        return None
    if target.id == ctx.bot.id:
        await ctx.reply("I won't do that to myself 🙂")
        return None
    current = await resolve_role(ctx.session, ctx.bot, ctx.cache, ctx.settings, target.id, ctx.tenant)
    if not can_manage(ctx.role, current):
        await ctx.reply("🚫 You can only act on people below your own role.")
        return None
    return target, rest


async def _tg(ctx: Ctx, coro) -> bool:
    try:
        await coro
        return True
    except TelegramAPIError as exc:
        await ctx.reply(f"⚠️ Telegram refused: {escape(str(exc))}\nMake sure I'm an admin with the right permissions.")
        return False


def _reason(rest: list[str]) -> str:
    return " ".join(rest)[:256]


def _who(t: Target) -> str:
    return f'<a href="tg://user?id={t.id}">{escape(t.name)}</a>'


# --- bans / kicks / mutes --------------------------------------------------------------------------

@command("ban", description="Ban a user from the group", category=CAT, usage="/ban <reply|id|@user> [reason]",
         permission=Role.ADMIN, scope="group")
async def ban(ctx: Ctx) -> None:
    g = await _guard(ctx, "/ban @user reason")
    if g and await _tg(ctx, svc.do_ban(ctx.bot, ctx.message.chat.id, g[0].id)):
        await svc.log_action(ctx.session, ctx.tenant, "ban", g[0].id, ctx.user_id, _reason(g[1]))  # type: ignore[arg-type]
        await ctx.reply(f"🔨 Banned {_who(g[0])}." + (f"\nReason: {escape(_reason(g[1]))}" if g[1] else ""))


@command("unban", description="Lift a ban", category=CAT, usage="/unban <id|@user>", permission=Role.ADMIN, scope="group")
async def unban(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/unban 12345</code> (banned users can't be replied to; use their ID or @username)")
        return
    if await _tg(ctx, svc.do_unban(ctx.bot, ctx.message.chat.id, target.id)):
        await svc.log_action(ctx.session, ctx.tenant, "unban", target.id, ctx.user_id)  # type: ignore[arg-type]
        await ctx.reply(f"✅ Unbanned {_who(target)}.")


@command("tban", description="Temporarily ban a user", category=CAT, usage="/tban <user> <10m|2h|7d> [reason]",
         examples=("/tban @spammer 1d spam",), permission=Role.ADMIN, scope="group")
async def tban(ctx: Ctx) -> None:
    g = await _guard(ctx, "/tban @user 1d reason")
    if not g:
        return
    delta = svc.parse_duration(g[1][0]) if g[1] else None
    if delta is None:
        await ctx.reply("Give a duration between 1m and 366d, like <code>30m</code>, <code>2h</code>, <code>7d</code>.")
        return
    until = utcnow() + delta
    if await _tg(ctx, svc.do_ban(ctx.bot, ctx.message.chat.id, g[0].id, until)):
        await svc.log_action(ctx.session, ctx.tenant, "tban", g[0].id, ctx.user_id, _reason(g[1][1:]), until)  # type: ignore[arg-type]
        await ctx.reply(f"⏳ Banned {_who(g[0])} for {svc.fmt_duration(delta)}.")


@command("kick", description="Remove a user (they can rejoin)", category=CAT, usage="/kick <reply|id|@user> [reason]",
         permission=Role.MODERATOR, scope="group")
async def kick(ctx: Ctx) -> None:
    g = await _guard(ctx, "/kick @user reason")
    if g and await _tg(ctx, svc.do_kick(ctx.bot, ctx.message.chat.id, g[0].id)):
        await svc.log_action(ctx.session, ctx.tenant, "kick", g[0].id, ctx.user_id, _reason(g[1]))  # type: ignore[arg-type]
        await ctx.reply(f"👢 Kicked {_who(g[0])}.")


@command("mute", description="Stop a user from sending messages", category=CAT, usage="/mute <reply|id|@user> [reason]",
         permission=Role.MODERATOR, scope="group")
async def mute(ctx: Ctx) -> None:
    g = await _guard(ctx, "/mute @user reason")
    if g and await _tg(ctx, svc.do_mute(ctx.bot, ctx.message.chat.id, g[0].id)):
        await svc.log_action(ctx.session, ctx.tenant, "mute", g[0].id, ctx.user_id, _reason(g[1]))  # type: ignore[arg-type]
        await ctx.reply(f"🔇 Muted {_who(g[0])}.")


@command("tmute", description="Temporarily mute a user", category=CAT, usage="/tmute <user> <10m|2h|7d> [reason]",
         examples=("/tmute @alex 30m flooding",), permission=Role.MODERATOR, scope="group")
async def tmute(ctx: Ctx) -> None:
    g = await _guard(ctx, "/tmute @user 30m reason")
    if not g:
        return
    delta = svc.parse_duration(g[1][0]) if g[1] else None
    if delta is None:
        await ctx.reply("Give a duration between 1m and 366d, like <code>30m</code> or <code>2h</code>.")
        return
    until = utcnow() + delta
    if await _tg(ctx, svc.do_mute(ctx.bot, ctx.message.chat.id, g[0].id, until)):
        await svc.log_action(ctx.session, ctx.tenant, "tmute", g[0].id, ctx.user_id, _reason(g[1][1:]), until)  # type: ignore[arg-type]
        await ctx.reply(f"🔇 Muted {_who(g[0])} for {svc.fmt_duration(delta)}.")


@command("unmute", description="Allow a muted user to talk again", category=CAT, usage="/unmute <reply|id|@user>",
         permission=Role.MODERATOR, scope="group")
async def unmute(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/unmute @user</code>")
        return
    if await _tg(ctx, svc.do_unmute(ctx.bot, ctx.message.chat.id, target.id)):
        await svc.log_action(ctx.session, ctx.tenant, "unmute", target.id, ctx.user_id)  # type: ignore[arg-type]
        await ctx.reply(f"🔊 Unmuted {_who(target)}.")


# --- warnings ----------------------------------------------------------------------------------------

@command("warn", description="Warn a user (thresholds can mute/kick/ban)", category=CAT,
         usage="/warn <reply|id|@user> [reason]", permission=Role.MODERATOR, scope="group")
async def warn(ctx: Ctx) -> None:
    g = await _guard(ctx, "/warn @user reason")
    if not g:
        return
    count, action = await svc.apply_warn(ctx.session, ctx.bot, ctx.tenant, g[0].id, ctx.user_id, _reason(g[1]))  # type: ignore[arg-type]
    text = f"⚠️ Warned {_who(g[0])} — now at <b>{count}</b> warning(s)."
    if g[1]:
        text += f"\nReason: {escape(_reason(g[1]))}"
    if action:
        text += f"\nThreshold reached → <b>{escape(action)}</b>."
    await ctx.reply(text)


@command("unwarn", description="Remove a user's latest warning", category=CAT, usage="/unwarn <reply|id|@user>",
         permission=Role.MODERATOR, scope="group")
async def unwarn(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/unwarn @user</code>")
        return
    row = (await ctx.session.execute(
        select(ModWarning).where(ModWarning.tenant_id == ctx.tenant.id, ModWarning.user_id == target.id)
        .order_by(ModWarning.id.desc()).limit(1)
    )).scalars().first()
    if row is None:
        await ctx.reply("They have no warnings.")
        return
    await ctx.session.delete(row)
    await svc.log_action(ctx.session, ctx.tenant, "unwarn", target.id, ctx.user_id)
    await ctx.reply(f"✅ Removed one warning from {_who(target)}.")


@command("warnings", description="Show warnings for a user (or yourself)", category=CAT,
         usage="/warnings [reply|id|@user]", scope="group")
async def warnings(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    if target is None or (ctx.role < Role.MODERATOR and target.id != ctx.user_id):
        target = Target(ctx.user_id, ctx.user.first_name or "You")
    rows = (await ctx.session.execute(
        select(ModWarning).where(ModWarning.tenant_id == ctx.tenant.id, ModWarning.user_id == target.id)
        .order_by(ModWarning.id.desc()).limit(10)
    )).scalars().all()
    if not rows:
        await ctx.reply(f"{_who(target)} has no warnings. 🎉")
        return
    lines = [f"<b>⚠️ Warnings for {_who(target)}</b> ({len(rows)} shown)"]
    lines += [f"• {r.created_at:%m-%d} — {escape(r.reason or 'no reason')}" for r in rows]
    await ctx.reply("\n".join(lines))


@command("resetwarns", description="Clear all warnings of a user", category=CAT, aliases=("clearwarns",),
         usage="/resetwarns <reply|id|@user>", permission=Role.ADMIN, scope="group")
async def resetwarns(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/resetwarns @user</code>")
        return
    await ctx.session.execute(delete(ModWarning).where(
        ModWarning.tenant_id == ctx.tenant.id, ModWarning.user_id == target.id))
    await svc.log_action(ctx.session, ctx.tenant, "resetwarns", target.id, ctx.user_id)
    await ctx.reply(f"🧹 Cleared all warnings of {_who(target)}.")


@command("warnrule", description="Set what happens at N warnings (mute/kick/ban/none)", category=CAT,
         usage="/warnrule <count> <mute|kick|ban|none>", examples=("/warnrule 3 mute", "/warnrule 7 ban"),
         permission=Role.ADMIN, scope="group")
async def warnrule(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    a = ctx.args
    if len(a) != 2 or not a[0].isdigit() or not 1 <= int(a[0]) <= 50 or a[1].lower() not in ("mute", "kick", "ban", "none"):
        await ctx.reply("Usage: <code>/warnrule 3 mute</code> (count 1-50; actions: mute, kick, ban, none)")
        return
    cfg = svc.mod_cfg(ctx.tenant)
    if a[1].lower() == "none":
        cfg["warn_actions"].pop(a[0], None)
    else:
        cfg["warn_actions"][a[0]] = a[1].lower()
    svc.save_mod_cfg(ctx.tenant, cfg)
    await ctx.reply("✅ Saved. Use /warnrules to review.")


@command("warnrules", description="Show the warning thresholds", category=CAT, permission=Role.MODERATOR, scope="group")
async def warnrules(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    cfg = svc.mod_cfg(ctx.tenant)
    rules = sorted(cfg["warn_actions"].items(), key=lambda kv: int(kv[0]))
    lines = ["<b>📏 Warning thresholds</b>"] + [f"• {k} warnings → {v}" for k, v in rules]
    if not rules:
        lines.append("None set. Add one with <code>/warnrule 3 mute</code>.")
    lines.append(f"Mute length: {cfg['warn_mute_hours']}h (change with /warnmute)")
    await ctx.reply("\n".join(lines))


@command("warnmute", description="Set how many hours a warning-mute lasts", category=CAT, usage="/warnmute <hours>",
         permission=Role.ADMIN, scope="group")
async def warnmute(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not ctx.args or not ctx.args[0].isdigit() or not 1 <= int(ctx.args[0]) <= 8760:
        await ctx.reply("Usage: <code>/warnmute 24</code> (1-8760 hours)")
        return
    cfg = svc.mod_cfg(ctx.tenant)
    cfg["warn_mute_hours"] = int(ctx.args[0])
    svc.save_mod_cfg(ctx.tenant, cfg)
    await ctx.reply(f"✅ Warning mutes now last {ctx.args[0]}h.")


# --- cleanup ---------------------------------------------------------------------------------------------

@command("del", description="Delete the replied-to message", category=CAT, aliases=("delete",),
         permission=Role.MODERATOR, scope="group")
async def del_cmd(ctx: Ctx) -> None:
    r = ctx.message.reply_to_message
    if r is None:
        await ctx.reply("Reply to the message you want to delete.")
        return
    await _tg(ctx, ctx.bot.delete_messages(ctx.message.chat.id, [r.message_id, ctx.message.message_id]))


@command("purge", description="Delete many messages: reply to the first one, or give a count", category=CAT,
         usage="/purge [count 1-100]  (or reply to the first message to delete)",
         permission=Role.MODERATOR, scope="group", cooldown=10)
async def purge(ctx: Ctx) -> None:
    cur = ctx.message.message_id
    if ctx.message.reply_to_message:
        start = ctx.message.reply_to_message.message_id
    elif ctx.args and ctx.args[0].isdigit():
        start = cur - min(max(int(ctx.args[0]), 1), 100)
    else:
        await ctx.reply("Reply to the first message to delete, or use <code>/purge 20</code>.")
        return
    ids = list(range(max(start, cur - 100), cur + 1))
    if await _tg(ctx, ctx.bot.delete_messages(ctx.message.chat.id, ids)):
        await svc.log_action(ctx.session, ctx.tenant, "purge", 0, ctx.user_id, f"{len(ids)} ids")  # type: ignore[arg-type]


# --- locks --------------------------------------------------------------------------------------------------

@command("lock", description="Restrict what everyone can send", category=CAT,
         usage="/lock <messages|media|stickers|polls|previews>", examples=("/lock media",),
         permission=Role.ADMIN, scope="group")
async def lock(ctx: Ctx) -> None:
    await _set_lock(ctx, locked=True)


@command("unlock", description="Re-allow a locked content type", category=CAT,
         usage="/unlock <messages|media|stickers|polls|previews|all>", permission=Role.ADMIN, scope="group")
async def unlock(ctx: Ctx) -> None:
    await _set_lock(ctx, locked=False)


async def _set_lock(ctx: Ctx, locked: bool) -> None:
    kind = ctx.raw_args.strip().lower()
    kinds = list(svc.LOCK_FIELDS) if kind == "all" and not locked else [kind]
    if any(k not in svc.LOCK_FIELDS for k in kinds):
        await ctx.reply("Choose one of: " + ", ".join(f"<code>{k}</code>" for k in svc.LOCK_FIELDS) + ("" if locked else ", <code>all</code>"))
        return
    try:
        chat = await ctx.bot.get_chat(ctx.message.chat.id)
        perms = chat.permissions or svc.FULL
        update = {f: (not locked) for k in kinds for f in svc.LOCK_FIELDS[k]}
        await ctx.bot.set_chat_permissions(ctx.message.chat.id, perms.model_copy(update=update))
    except TelegramAPIError as exc:
        await ctx.reply(f"⚠️ Telegram refused: {escape(str(exc))}")
        return
    await svc.log_action(ctx.session, ctx.tenant, "lock" if locked else "unlock", 0, ctx.user_id, ",".join(kinds))  # type: ignore[arg-type]
    await ctx.reply(f"{'🔒 Locked' if locked else '🔓 Unlocked'}: <b>{', '.join(kinds)}</b>")


@command("locks", description="Show what is currently locked", category=CAT, permission=Role.MODERATOR, scope="group")
async def locks(ctx: Ctx) -> None:
    try:
        perms = (await ctx.bot.get_chat(ctx.message.chat.id)).permissions
    except TelegramAPIError as exc:
        await ctx.reply(f"⚠️ {escape(str(exc))}")
        return
    if perms is None:
        await ctx.reply("I couldn't read this chat's permissions.")
        return
    lines = ["<b>🔐 Locks</b>"]
    for kind, fields in svc.LOCK_FIELDS.items():
        locked = any(getattr(perms, f, True) is False for f in fields)
        lines.append(f"{'🔒' if locked else '🔓'} {kind}")
    await ctx.reply("\n".join(lines))


# --- bot-enforced auto moderation -------------------------------------------------------------------------------

async def _toggle_flag(ctx: Ctx, flag: str) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, svc.FLAG_FEATURE[flag]):
        return
    arg = (ctx.args[0].lower() if ctx.args else "")
    cfg = svc.mod_cfg(ctx.tenant)
    if arg not in ("on", "off"):
        await ctx.reply(f"/{flag} is <b>{'on' if cfg['automod'][flag] else 'off'}</b>. Use <code>/{flag} on</code> or <code>/{flag} off</code>.")
        return
    cfg["automod"][flag] = arg == "on"
    if flag == "antiflood" and len(ctx.args) > 1 and ctx.args[1].isdigit():
        cfg["automod"]["flood_limit"] = min(max(int(ctx.args[1]), 3), 30)
    svc.save_mod_cfg(ctx.tenant, cfg)
    await svc.log_action(ctx.session, ctx.tenant, flag, 0, ctx.user_id, arg)
    await ctx.reply(f"{'✅' if arg == 'on' else '🚫'} <b>{flag}</b> is now {arg}.")


@command("antilink", description="Delete messages containing links (non-staff)", category=CAT,
         usage="/antilink <on|off>", permission=Role.ADMIN, scope="group")
async def antilink(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antilink")


@command("antiflood", description="Mute users who send too many messages too fast", category=CAT,
         usage="/antiflood <on|off> [messages per 10s, 3-30]", examples=("/antiflood on 6",),
         permission=Role.ADMIN, scope="group")
async def antiflood(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antiflood")


@command("antiinvite", description="Delete Telegram invite links", category=CAT, usage="/antiinvite <on|off>",
         permission=Role.ADMIN, scope="group")
async def antiinvite(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antiinvite")


@command("antiforward", description="Delete forwarded messages", category=CAT, usage="/antiforward <on|off>",
         permission=Role.ADMIN, scope="group")
async def antiforward(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antiforward")


@command("antibot", description="Remove bots added by non-admins", category=CAT, usage="/antibot <on|off>",
         permission=Role.ADMIN, scope="group")
async def antibot(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antibot")


@command("antimedia", description="Delete photos, videos, files and stickers from non-staff", category=CAT,
         usage="/antimedia <on|off>", permission=Role.ADMIN, scope="group")
async def antimedia(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antimedia")


@command("antispam", description="Delete repeated identical messages", category=CAT, usage="/antispam <on|off>",
         permission=Role.ADMIN, scope="group")
async def antispam(ctx: Ctx) -> None:
    await _toggle_flag(ctx, "antispam")


@command("slowmode", description="Limit how often each member may post (enforced by the bot)", category=CAT,
         usage="/slowmode <seconds|off>", examples=("/slowmode 30",), permission=Role.ADMIN, scope="group")
async def slowmode(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, "automod_advanced"):
        return
    arg = ctx.args[0].lower() if ctx.args else ""
    cfg = svc.mod_cfg(ctx.tenant)
    if arg == "off":
        cfg["slowmode"] = 0
    elif arg.isdigit() and 3 <= int(arg) <= 3600:
        cfg["slowmode"] = int(arg)
    else:
        await ctx.reply(f"Slow mode: <b>{cfg['slowmode'] or 'off'}</b>. Use <code>/slowmode 30</code> or <code>/slowmode off</code>.")
        return
    svc.save_mod_cfg(ctx.tenant, cfg)
    await ctx.reply(f"🐢 Slow mode: <b>{cfg['slowmode'] or 'off'}</b>" + (" seconds between messages." if cfg["slowmode"] else ""))


@command("automod", description="Show all auto-moderation settings", category=CAT, permission=Role.ADMIN, scope="group")
async def automod(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    cfg = svc.mod_cfg(ctx.tenant)
    plan = await plan_of(ctx)
    lines = [f"<b>🤖 Auto-moderation</b> (plan: {plan.name})"]
    for flag, feat in svc.FLAG_FEATURE.items():
        state = "✅ on" if cfg["automod"][flag] else "off"
        lock = "" if plan.has(feat) else " 🔒"
        lines.append(f"• {flag}: {state}{lock}")
    lines.append(f"• flood limit: {cfg['automod']['flood_limit']} msgs/10s")
    lines.append(f"• slow mode: {cfg['slowmode'] or 'off'}")
    lines.append(f"• word filters: {len(cfg['filters'])}/{plan.limit('filters')} (action: {cfg['filter_action']})")
    await ctx.reply("\n".join(lines))


# --- word filters --------------------------------------------------------------------------------------------------------

@command("addfilter", description="Block a word or phrase", category=CAT, usage="/addfilter <word or phrase>",
         permission=Role.ADMIN, scope="group")
async def addfilter(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    word = ctx.raw_args.strip().lower()[:40]
    if not word:
        await ctx.reply("Usage: <code>/addfilter badword</code>")
        return
    cfg = svc.mod_cfg(ctx.tenant)
    plan = await plan_of(ctx)
    if word in cfg["filters"]:
        await ctx.reply("That filter already exists.")
        return
    if len(cfg["filters"]) >= plan.limit("filters"):
        await ctx.reply(f"🔒 Your plan allows {plan.limit('filters')} filters. Upgrade with /plans for more.")
        return
    cfg["filters"].append(word)
    svc.save_mod_cfg(ctx.tenant, cfg)
    await ctx.reply(f"✅ Filter added: <code>{escape(word)}</code>")


@command("delfilter", description="Remove a blocked word", category=CAT, usage="/delfilter <word>",
         permission=Role.ADMIN, scope="group")
async def delfilter(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    word = ctx.raw_args.strip().lower()
    cfg = svc.mod_cfg(ctx.tenant)
    if word not in cfg["filters"]:
        await ctx.reply("No such filter. See /filters.")
        return
    cfg["filters"].remove(word)
    svc.save_mod_cfg(ctx.tenant, cfg)
    await ctx.reply(f"🗑 Removed <code>{escape(word)}</code>.")


@command("filters", description="List blocked words", category=CAT, permission=Role.MODERATOR, scope="group")
async def filters_cmd(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    cfg = svc.mod_cfg(ctx.tenant)
    if not cfg["filters"]:
        await ctx.reply("No word filters yet. Add one with <code>/addfilter word</code>.")
        return
    await ctx.reply("<b>🚫 Filters</b>\n" + "\n".join(f"• <code>{escape(w)}</code>" for w in cfg["filters"]))


@command("filteraction", description="Choose what a filter hit does: delete or warn", category=CAT,
         usage="/filteraction <delete|warn>", permission=Role.ADMIN, scope="group")
async def filteraction(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg not in ("delete", "warn"):
        await ctx.reply("Usage: <code>/filteraction delete</code> or <code>/filteraction warn</code>")
        return
    cfg = svc.mod_cfg(ctx.tenant)
    cfg["filter_action"] = arg
    svc.save_mod_cfg(ctx.tenant, cfg)
    await ctx.reply(f"✅ Filter hits will now: <b>{arg}</b>.")


# --- records ---------------------------------------------------------------------------------------------------------------

@command("modlog", description="Recent moderation actions in this group", category=CAT, usage="/modlog [user]",
         permission=Role.MODERATOR, scope="group")
async def modlog(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    q = select(ModAction).where(ModAction.tenant_id == ctx.tenant.id).order_by(ModAction.id.desc()).limit(10)
    if target:
        q = q.where(ModAction.target_id == target.id)
    rows = (await ctx.session.execute(q)).scalars().all()
    if not rows:
        await ctx.reply("Nothing logged yet.")
        return
    lines = ["<b>📋 Moderation log</b>"]
    for r in rows:
        who = f"<code>{r.target_id}</code>" if r.target_id else "—"
        lines.append(f"{r.created_at:%m-%d %H:%M} · <b>{escape(r.action)}</b> → {who} by <code>{r.moderator_id or 'auto'}</code>"
                     + (f" · {escape(r.reason)}" if r.reason else ""))
    await ctx.reply("\n".join(lines))


@command("modnote", description="Attach a private note to a user", category=CAT, usage="/modnote <user> <note>",
         permission=Role.MODERATOR, scope="group")
async def modnote(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, rest = await ctx.resolve_target()
    note = " ".join(rest).strip()[:500]
    if target is None or not note:
        await ctx.reply("Usage: <code>/modnote @user text of the note</code>")
        return
    ctx.session.add(ModNote(tenant_id=ctx.tenant.id, user_id=target.id, author_id=ctx.user_id, note=note))
    await ctx.reply(f"📝 Note saved for {_who(target)}.")


@command("notes", description="Show moderator notes about a user", category=CAT, usage="/notes <user>",
         permission=Role.MODERATOR, scope="group")
async def notes(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/notes @user</code>")
        return
    rows = (await ctx.session.execute(
        select(ModNote).where(ModNote.tenant_id == ctx.tenant.id, ModNote.user_id == target.id)
        .order_by(ModNote.id.desc()).limit(10)
    )).scalars().all()
    if not rows:
        await ctx.reply("No notes for that user.")
        return
    lines = [f"<b>📝 Notes on {_who(target)}</b>"]
    lines += [f"#{r.id} · {r.created_at:%Y-%m-%d} · {escape(r.note)}" for r in rows]
    await ctx.reply("\n".join(lines))


@command("delnote", description="Delete a moderator note by its number", category=CAT, usage="/delnote <number>",
         permission=Role.MODERATOR, scope="group")
async def delnote(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not ctx.args or not ctx.args[0].lstrip("#").isdigit():
        await ctx.reply("Usage: <code>/delnote 12</code>")
        return
    res = await ctx.session.execute(delete(ModNote).where(
        ModNote.tenant_id == ctx.tenant.id, ModNote.id == int(ctx.args[0].lstrip("#"))))
    await ctx.reply("🗑 Deleted." if res.rowcount else "No such note in this group.")


@command("userhistory", description="Warnings, actions and notes for a user", category=CAT, usage="/userhistory <user>",
         permission=Role.MODERATOR, scope="group")
async def userhistory(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/userhistory @user</code>")
        return
    tid = ctx.tenant.id
    warns = await svc.warning_count(ctx.session, tid, target.id)
    acts = (await ctx.session.execute(
        select(ModAction.action, func.count()).where(ModAction.tenant_id == tid, ModAction.target_id == target.id)
        .group_by(ModAction.action))).all()
    n_notes = int((await ctx.session.execute(
        select(func.count()).select_from(ModNote).where(ModNote.tenant_id == tid, ModNote.user_id == target.id)
    )).scalar_one())
    rec = await users_repo.get_user(ctx.session, target.id)
    lines = [f"<b>🗂 History of {_who(target)}</b>", f"ID: <code>{target.id}</code>", f"Warnings: <b>{warns}</b>",
             f"Notes: <b>{n_notes}</b>"]
    lines.append("Actions: " + (", ".join(f"{a}×{c}" for a, c in acts) if acts else "none"))
    if rec:
        lines.append(f"First seen by the bot: {rec.created_at:%Y-%m-%d}")
    await ctx.reply("\n".join(lines))


@command("modstats", description="Moderation activity in the last 7 days", category=CAT, permission=Role.MODERATOR, scope="group")
async def modstats(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    since = utcnow() - timedelta(days=7)
    rows = (await ctx.session.execute(
        select(ModAction.action, func.count()).where(ModAction.tenant_id == ctx.tenant.id, ModAction.created_at >= since)
        .group_by(ModAction.action))).all()
    if not rows:
        await ctx.reply("No moderation actions in the last 7 days.")
        return
    await ctx.reply("<b>📊 Last 7 days</b>\n" + "\n".join(f"• {a}: {c}" for a, c in sorted(rows)))


@command("report", description="Report the replied-to message to the admins", category=CAT, usage="/report [reason]",
         scope="group", cooldown=60)
async def report(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    r = ctx.message.reply_to_message
    if r is None or r.from_user is None:
        await ctx.reply("Reply to the message you want to report.")
        return
    try:
        admins = [a.user for a in await ctx.bot.get_chat_administrators(ctx.message.chat.id) if not a.user.is_bot]
    except TelegramAPIError:
        admins = []
    pings = " ".join(f'<a href="tg://user?id={a.id}">\u200b</a>' for a in admins[:20])
    await svc.log_action(ctx.session, ctx.tenant, "report", r.from_user.id, ctx.user_id, ctx.raw_args[:200])
    await ctx.reply(f"🚨 Report sent to the admins.{pings}")
