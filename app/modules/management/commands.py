from __future__ import annotations

from datetime import timedelta
from html import escape

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import delete, func, select

from app.bot.context import Ctx
from app.bot.registry import command
from app.database.models import CustomCommand, MemberEvent, ScheduledMessage, Trigger
from app.modules.management import service as ms
from app.modules.moderation.service import parse_duration
from app.services.gates import plan_of, require_feature
from app.services.roles import Role
from app.utils.telegram import safe_reply
from app.utils.time import utcnow

CAT = "management"


async def _tg(ctx: Ctx, coro) -> bool:
    try:
        await coro
        return True
    except TelegramAPIError as exc:
        await ctx.reply(f"⚠️ Telegram refused: {escape(str(exc))}\nMake sure I'm an admin with the right permissions.")
        return False


# --- welcome / goodbye / rules ------------------------------------------------------------------

async def _msg_setting(ctx: Ctx, key: str, setter: bool) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, "welcome"):
        return
    cfg = ms.mg_cfg(ctx.tenant)
    if setter:
        text = ctx.raw_args.strip()[:1000]
        if not text:
            await ctx.reply(f"Usage: <code>/set{key} Welcome {{mention}} to {{title}}!</code>\n"
                            "Placeholders: {name} {mention} {title} {count}")
            return
        cfg[key]["text"] = text
        cfg[key]["on"] = True
        ms.save_mg_cfg(ctx.tenant, cfg)
        await ctx.reply(f"✅ {key.title()} message saved and turned on.")
        return
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg in ("on", "off"):
        cfg[key]["on"] = arg == "on"
        ms.save_mg_cfg(ctx.tenant, cfg)
        await ctx.reply(f"{'✅' if arg == 'on' else '🚫'} {key.title()} messages are now {arg}.")
        return
    preview = ms.render(cfg[key]["text"], ctx.user_id, ctx.user.first_name or "Friend", ctx.tenant.title, "N")
    await safe_reply(ctx.message, f"<b>{key.title()}</b> is <b>{'on' if cfg[key]['on'] else 'off'}</b>. Preview:\n\n{preview}")


@command("setwelcome", description="Set the welcome message", category=CAT,
         usage="/setwelcome <text with {mention} {title} {count}>", permission=Role.ADMIN, scope="group")
async def setwelcome(ctx: Ctx) -> None:
    await _msg_setting(ctx, "welcome", True)


@command("welcome", description="Show or toggle the welcome message", category=CAT, usage="/welcome [on|off]",
         permission=Role.ADMIN, scope="group")
async def welcome(ctx: Ctx) -> None:
    await _msg_setting(ctx, "welcome", False)


@command("setgoodbye", description="Set the goodbye message", category=CAT, usage="/setgoodbye <text>",
         permission=Role.ADMIN, scope="group")
async def setgoodbye(ctx: Ctx) -> None:
    await _msg_setting(ctx, "goodbye", True)


@command("goodbye", description="Show or toggle the goodbye message", category=CAT, usage="/goodbye [on|off]",
         permission=Role.ADMIN, scope="group")
async def goodbye(ctx: Ctx) -> None:
    await _msg_setting(ctx, "goodbye", False)


@command("setrules", description="Set the group rules", category=CAT, usage="/setrules <text>",
         permission=Role.ADMIN, scope="group")
async def setrules(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    text = ctx.raw_args.strip()[:3000]
    if not text:
        await ctx.reply("Usage: <code>/setrules 1. Be kind\n2. No spam</code>")
        return
    cfg = ms.mg_cfg(ctx.tenant)
    cfg["rules"] = text
    ms.save_mg_cfg(ctx.tenant, cfg)
    await ctx.reply("✅ Rules saved. Members can read them with /rules.")


@command("rules", description="Show the group rules", category=CAT, scope="group")
async def rules(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    text = ms.mg_cfg(ctx.tenant)["rules"]
    if not text:
        await ctx.reply("No rules have been set yet.")
        return
    await safe_reply(ctx.message, f"<b>📜 Rules</b>\n\n{text}")


# --- captcha -----------------------------------------------------------------------------------------

@command("captcha", description="Require new members to press a button", category=CAT,
         usage="/captcha <on|off|minutes 1-60>", examples=("/captcha on", "/captcha 10"),
         permission=Role.ADMIN, scope="group")
async def captcha(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, "captcha"):
        return
    cfg = ms.mg_cfg(ctx.tenant)
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg in ("on", "off"):
        cfg["captcha"]["on"] = arg == "on"
    elif arg.isdigit() and 1 <= int(arg) <= 60:
        cfg["captcha"]["minutes"] = int(arg)
    else:
        await ctx.reply(f"Captcha is <b>{'on' if cfg['captcha']['on'] else 'off'}</b> "
                        f"({cfg['captcha']['minutes']} min to answer). Use <code>/captcha on</code>, "
                        "<code>/captcha off</code> or <code>/captcha 10</code>.")
        return
    ms.save_mg_cfg(ctx.tenant, cfg)
    await ctx.reply(f"✅ Captcha: {'on' if cfg['captcha']['on'] else 'off'}, {cfg['captcha']['minutes']} min.\n"
                    "I need the <b>Restrict members</b> and <b>Delete messages</b> admin rights.")


# --- pins & chat tools ----------------------------------------------------------------------------------

@command("pin", description="Pin the replied-to message", category=CAT, permission=Role.MODERATOR, scope="group")
async def pin(ctx: Ctx) -> None:
    r = ctx.message.reply_to_message
    if r is None:
        await ctx.reply("Reply to the message you want to pin.")
        return
    if await _tg(ctx, ctx.bot.pin_chat_message(ctx.message.chat.id, r.message_id, disable_notification=True)):
        await ctx.reply("📌 Pinned.")


@command("unpin", description="Unpin the replied-to (or latest) message", category=CAT,
         permission=Role.MODERATOR, scope="group")
async def unpin(ctx: Ctx) -> None:
    r = ctx.message.reply_to_message
    if await _tg(ctx, ctx.bot.unpin_chat_message(ctx.message.chat.id, r.message_id if r else None)):
        await ctx.reply("📌 Unpinned.")


@command("unpinall", description="Unpin every pinned message", category=CAT, permission=Role.ADMIN, scope="group")
async def unpinall(ctx: Ctx) -> None:
    if await _tg(ctx, ctx.bot.unpin_all_chat_messages(ctx.message.chat.id)):
        await ctx.reply("📌 All messages unpinned.")


@command("settitle", description="Change the group title", category=CAT, usage="/settitle <title>",
         permission=Role.ADMIN, scope="group")
async def settitle(ctx: Ctx) -> None:
    title = ctx.raw_args.strip()[:128]
    if not title:
        await ctx.reply("Usage: <code>/settitle New title</code>")
        return
    if await _tg(ctx, ctx.bot.set_chat_title(ctx.message.chat.id, title)):
        await ctx.reply("✅ Title changed.")


@command("setdesc", description="Change the group description", category=CAT, usage="/setdesc <text>",
         permission=Role.ADMIN, scope="group")
async def setdesc(ctx: Ctx) -> None:
    if await _tg(ctx, ctx.bot.set_chat_description(ctx.message.chat.id, ctx.raw_args.strip()[:255])):
        await ctx.reply("✅ Description updated.")


@command("members", description="How many members the group has", category=CAT, scope="group")
async def members(ctx: Ctx) -> None:
    try:
        n = await ctx.bot.get_chat_member_count(ctx.message.chat.id)
    except TelegramAPIError as exc:
        await ctx.reply(f"⚠️ {escape(str(exc))}")
        return
    await ctx.reply(f"👥 <b>{n}</b> members")


@command("admins", description="List the group's admins", category=CAT, scope="group", cooldown=10)
async def admins(ctx: Ctx) -> None:
    try:
        rows = await ctx.bot.get_chat_administrators(ctx.message.chat.id)
    except TelegramAPIError as exc:
        await ctx.reply(f"⚠️ {escape(str(exc))}")
        return
    lines = ["<b>🛡 Admins</b>"]
    for a in rows:
        if a.user.is_bot:
            continue
        crown = "👑 " if a.status == "creator" else "• "
        lines.append(f"{crown}{escape(a.user.full_name)}")
    await ctx.reply("\n".join(lines))


@command("joins", description="Joins and leaves over the last N days", category=CAT, usage="/joins [days 1-90]",
         permission=Role.MODERATOR, scope="group")
async def joins(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    days = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 7
    days = min(max(days, 1), 90)
    since = utcnow() - timedelta(days=days)
    rows = dict((await ctx.session.execute(
        select(MemberEvent.kind, func.count()).where(MemberEvent.tenant_id == ctx.tenant.id, MemberEvent.created_at >= since)
        .group_by(MemberEvent.kind))).all())
    j, left = rows.get("join", 0), rows.get("leave", 0)
    await ctx.reply(f"📈 Last {days} day(s): <b>{j}</b> joined, <b>{left}</b> left (net {j - left:+d}).")


@command("groupsettings", description="Summary of this group's configuration", category=CAT,
         permission=Role.ADMIN, scope="group")
async def groupsettings(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    cfg = ms.mg_cfg(ctx.tenant)
    plan = await plan_of(ctx)
    on = lambda b: "on" if b else "off"  # noqa: E731
    await ctx.reply(
        f"<b>⚙️ {escape(ctx.tenant.title or 'Group')}</b>\n"
        f"Plan: <b>{plan.name}</b> · Prefix: <code>{escape(ctx.tenant.prefix)}</code>\n"
        f"Welcome: {on(cfg['welcome']['on'])} · Goodbye: {on(cfg['goodbye']['on'])}\n"
        f"Captcha: {on(cfg['captcha']['on'])} · Rules: {'set' if cfg['rules'] else 'not set'}"
    )


# --- custom commands -------------------------------------------------------------------------------------------

@command("addcmd", description="Create a custom command", category=CAT, usage="/addcmd <name> <reply text>",
         examples=("/addcmd discord Join us: https://discord.gg/example",), permission=Role.ADMIN, scope="group")
async def addcmd(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, "custom_commands"):
        return
    name, _, text = ctx.raw_args.strip().partition(" ")
    name = name.lower().lstrip("/!")
    text = text.strip()[:2000]
    if not name or not text or not name.replace("_", "").isalnum() or len(name) > 32:
        await ctx.reply("Usage: <code>/addcmd name reply text</code> (name: letters, digits, underscore)")
        return
    from app.bot.registry import REGISTRY

    if REGISTRY.get(name) is not None:
        await ctx.reply("That name is used by a built-in command. Pick another.")
        return
    plan = await plan_of(ctx)
    count = int((await ctx.session.execute(
        select(func.count()).select_from(CustomCommand).where(CustomCommand.tenant_id == ctx.tenant.id))).scalar_one())
    existing = (await ctx.session.execute(select(CustomCommand).where(
        CustomCommand.tenant_id == ctx.tenant.id, CustomCommand.name == name))).scalars().first()
    if existing is None and count >= plan.limit("custom_commands"):
        await ctx.reply(f"🔒 Your plan allows {plan.limit('custom_commands')} custom commands. See /plans to upgrade.")
        return
    if existing:
        existing.response = text
    else:
        ctx.session.add(CustomCommand(tenant_id=ctx.tenant.id, name=name, response=text))
    await ctx.reply(f"✅ <code>/{name}</code> {'updated' if existing else 'created'}.")


@command("delcmd", description="Delete a custom command", category=CAT, usage="/delcmd <name>",
         permission=Role.ADMIN, scope="group")
async def delcmd(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    name = ctx.raw_args.strip().lower().lstrip("/!")
    res = await ctx.session.execute(delete(CustomCommand).where(
        CustomCommand.tenant_id == ctx.tenant.id, CustomCommand.name == name))
    await ctx.reply("🗑 Deleted." if res.rowcount else "No such custom command.")


@command("cmds", description="List this group's custom commands", category=CAT, scope="group")
async def cmds(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rows = (await ctx.session.execute(
        select(CustomCommand.name).where(CustomCommand.tenant_id == ctx.tenant.id).order_by(CustomCommand.name))).scalars().all()
    if not rows:
        await ctx.reply("No custom commands yet. Admins can add one with <code>/addcmd name text</code>.")
        return
    await ctx.reply("<b>🧩 Custom commands</b>\n" + "\n".join(f"• /{escape(n)}" for n in rows))


# --- keyword auto-replies -----------------------------------------------------------------------------------------------

@command("addtrigger", description="Auto-reply when a word appears", category=CAT,
         usage="/addtrigger <keyword> | <reply text>", examples=("/addtrigger price | See /plans for pricing",),
         permission=Role.ADMIN, scope="group")
async def addtrigger(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, "auto_replies"):
        return
    keyword, sep, text = ctx.raw_args.partition("|")
    keyword, text = keyword.strip().lower()[:64], text.strip()[:2000]
    if not sep or not keyword or not text:
        await ctx.reply("Usage: <code>/addtrigger keyword | reply text</code>")
        return
    plan = await plan_of(ctx)
    count = int((await ctx.session.execute(
        select(func.count()).select_from(Trigger).where(Trigger.tenant_id == ctx.tenant.id))).scalar_one())
    existing = (await ctx.session.execute(select(Trigger).where(
        Trigger.tenant_id == ctx.tenant.id, Trigger.keyword == keyword))).scalars().first()
    if existing is None and count >= plan.limit("triggers"):
        await ctx.reply(f"🔒 Your plan allows {plan.limit('triggers')} auto-replies. See /plans to upgrade.")
        return
    if existing:
        existing.response = text
    else:
        ctx.session.add(Trigger(tenant_id=ctx.tenant.id, keyword=keyword, response=text))
    await ms.invalidate_triggers(ctx.cache, ctx.tenant.id)
    await ctx.reply(f"✅ I'll answer when someone says <b>{escape(keyword)}</b>.")


@command("deltrigger", description="Remove an auto-reply", category=CAT, usage="/deltrigger <keyword>",
         permission=Role.ADMIN, scope="group")
async def deltrigger(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    res = await ctx.session.execute(delete(Trigger).where(
        Trigger.tenant_id == ctx.tenant.id, Trigger.keyword == ctx.raw_args.strip().lower()))
    await ms.invalidate_triggers(ctx.cache, ctx.tenant.id)
    await ctx.reply("🗑 Removed." if res.rowcount else "No such trigger.")


@command("triggers", description="List keyword auto-replies", category=CAT, permission=Role.MODERATOR, scope="group")
async def triggers_cmd(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rows = await ms.get_triggers(ctx.session, ctx.cache, ctx.tenant.id)
    if not rows:
        await ctx.reply("No auto-replies yet. Add one with <code>/addtrigger word | reply</code>.")
        return
    await ctx.reply("<b>💬 Auto-replies</b>\n" + "\n".join(f"• <code>{escape(k)}</code>" for k, _ in rows))


# --- scheduled messages -------------------------------------------------------------------------------------------------------

@command("schedule", description="Post a message later", category=CAT, usage="/schedule <10m|2h|1d> <text>",
         examples=("/schedule 2h Stream starts now!",), permission=Role.ADMIN, scope="group")
async def schedule(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not await require_feature(ctx, "scheduled_messages"):
        return
    when, _, text = ctx.raw_args.strip().partition(" ")
    delta = parse_duration(when)
    text = text.strip()[:3500]
    if delta is None or delta > timedelta(days=30) or not text:
        await ctx.reply("Usage: <code>/schedule 2h text</code> (1m to 30d)")
        return
    plan = await plan_of(ctx)
    pending = int((await ctx.session.execute(
        select(func.count()).select_from(ScheduledMessage).where(
            ScheduledMessage.tenant_id == ctx.tenant.id, ScheduledMessage.sent.is_(False)))).scalar_one())
    if pending >= plan.limit("scheduled"):
        await ctx.reply(f"🔒 Your plan allows {plan.limit('scheduled')} pending scheduled messages.")
        return
    row = ScheduledMessage(tenant_id=ctx.tenant.id, chat_id=ctx.message.chat.id, text=text,
                           run_at=utcnow() + delta, created_by=ctx.user_id)
    ctx.session.add(row)
    await ctx.session.flush()
    await ctx.reply(f"⏰ Scheduled #{row.id} for {row.run_at:%Y-%m-%d %H:%M} UTC (checked every ~30s).")


@command("schedules", description="List pending scheduled messages", category=CAT, permission=Role.ADMIN, scope="group")
async def schedules(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rows = (await ctx.session.execute(
        select(ScheduledMessage).where(ScheduledMessage.tenant_id == ctx.tenant.id, ScheduledMessage.sent.is_(False))
        .order_by(ScheduledMessage.run_at).limit(15))).scalars().all()
    if not rows:
        await ctx.reply("Nothing scheduled.")
        return
    await ctx.reply("<b>⏰ Scheduled</b>\n" + "\n".join(
        f"#{r.id} · {r.run_at:%m-%d %H:%M} UTC · {escape(r.text[:40])}" for r in rows))


@command("unschedule", description="Cancel a scheduled message", category=CAT, usage="/unschedule <number>",
         permission=Role.ADMIN, scope="group")
async def unschedule(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    if not ctx.args or not ctx.args[0].lstrip("#").isdigit():
        await ctx.reply("Usage: <code>/unschedule 3</code>")
        return
    res = await ctx.session.execute(delete(ScheduledMessage).where(
        ScheduledMessage.tenant_id == ctx.tenant.id, ScheduledMessage.id == int(ctx.args[0].lstrip("#")),
        ScheduledMessage.sent.is_(False)))
    await ctx.reply("🗑 Cancelled." if res.rowcount else "No such pending message in this group.")
