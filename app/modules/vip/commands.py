from __future__ import annotations

from datetime import timedelta
from sqlalchemy import func, select

from app.bot.context import Ctx
from app.bot.handlers.vip import vip_keyboard
from app.bot.registry import command
from app.database.models import VipMembership
from app.repositories import tenants as tenants_repo
from app.services import config
from app.services import vip as vip_service
from app.services.roles import Role
from app.utils.time import aware, utcnow

CAT = "vip"


def _status_text(m: VipMembership | None) -> str:
    if m is None:
        return "You are not a VIP member."
    exp = aware(m.expires_at)
    base = f"💠 VIP status: <b>{m.status}</b> · until {exp:%Y-%m-%d}"
    if m.status == "grace" and m.grace_until:
        base += f"\n⚠️ Grace period ends {aware(m.grace_until):%Y-%m-%d}."
    return base


@command("vip", description="Unlock or renew VIP access", category=CAT, scope="private")
async def vip(ctx: Ctx) -> None:
    cfg = await config.vip(ctx.session)
    m = await vip_service.get(ctx.session, ctx.user_id)
    lines = ["<b>💠 VIP</b>", _status_text(m), ""]
    error = await vip_service.eligibility_error(ctx.session, ctx.user_id)
    if error:
        lines.append(error)
        await ctx.reply("\n".join(lines))
        return
    lines.append("Choose a plan (paid in Telegram Stars). Renewing adds time to what you have left.")
    await ctx.reply("\n".join(lines), vip_keyboard(cfg))


@command("vipstatus", description="Check your VIP membership", category=CAT, scope="private")
async def vipstatus(ctx: Ctx) -> None:
    await ctx.reply(_status_text(await vip_service.get(ctx.session, ctx.user_id)))


@command("vipaccess", description="Get a fresh VIP channel invite link", category=CAT, scope="private", cooldown=30)
async def vipaccess(ctx: Ctx) -> None:
    if not vip_service.is_active(await vip_service.get(ctx.session, ctx.user_id)):
        await ctx.reply("You need an active VIP membership. Use /vip.")
        return
    ch = await vip_service.channel_id(ctx.session, ctx.settings)
    link = await vip_service.invite_link(ctx.bot, ch) if ch else None
    await ctx.reply(f"Request access here (approved automatically): {link}" if link
                    else "I couldn't create a link right now. The owner has to finish the VIP channel setup.")


@command("vipgrant", description="Give someone VIP days", category=CAT, usage="/vipgrant <id|@user> <days>",
         permission=Role.OWNER, scope="private")
async def vipgrant(ctx: Ctx) -> None:
    target, rest = await ctx.resolve_target()
    if target is None or not rest or not rest[0].isdigit() or not 1 <= int(rest[0]) <= 3650:
        await ctx.reply("Usage: <code>/vipgrant 12345 30</code>")
        return
    m = await vip_service.activate(ctx.session, target.id, "m", days=int(rest[0]))
    await tenants_repo.add_audit(ctx.session, "vip_granted", ctx.user_id, None, {"target": target.id, "days": int(rest[0])})
    await ctx.reply(f"✅ <code>{target.id}</code> has VIP until {aware(m.expires_at):%Y-%m-%d}.")


@command("viprevoke", description="Remove someone's VIP immediately", category=CAT, usage="/viprevoke <id|@user>",
         permission=Role.OWNER, scope="private")
async def viprevoke(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    if target is None:
        await ctx.reply("Usage: <code>/viprevoke 12345</code>")
        return
    m = await vip_service.get(ctx.session, target.id)
    if m is None:
        await ctx.reply("That user has no VIP record.")
        return
    m.status = "revoked"
    m.expires_at = utcnow()
    ch = await vip_service.channel_id(ctx.session, ctx.settings)
    if ch:
        await vip_service.revoke_access(ctx.bot, ch, target.id)
    await tenants_repo.add_audit(ctx.session, "vip_revoked", ctx.user_id, None, {"target": target.id})
    await ctx.reply("✅ VIP revoked and channel access removed.")


@command("viplist", description="VIP members and counts", category=CAT, permission=Role.SUPER_ADMIN, scope="private")
async def viplist(ctx: Ctx) -> None:
    counts = (await ctx.session.execute(
        select(VipMembership.status, func.count()).group_by(VipMembership.status))).all()
    soon = (await ctx.session.execute(
        select(VipMembership).where(VipMembership.status == "active", VipMembership.expires_at <= utcnow() + timedelta(days=7))
        .order_by(VipMembership.expires_at).limit(10))).scalars().all()
    lines = ["<b>💠 VIP</b>"] + [f"• {s}: {n}" for s, n in counts] if counts else ["No VIP members yet."]
    if soon:
        lines.append("\nExpiring within 7 days:")
        lines += [f"• <code>{m.user_id}</code> — {aware(m.expires_at):%Y-%m-%d}" for m in soon]
    await ctx.reply("\n".join(lines))


@command("vipconfig", description="View or edit VIP settings", category=CAT,
         usage="/vipconfig [monthly_stars|annual_stars|grace_days|channel_id|require_verification] <value>",
         examples=("/vipconfig monthly_stars 600", "/vipconfig channel_id -1001234567890"),
         permission=Role.OWNER, scope="private")
async def vipconfig(ctx: Ctx) -> None:
    cfg = await config.vip(ctx.session)
    keys = ("monthly_stars", "annual_stars", "grace_days", "channel_id", "require_verification")
    if not ctx.args:
        await ctx.reply("<b>💠 VIP settings</b>\n" + "\n".join(f"• {k}: <code>{cfg[k]}</code>" for k in keys)
                        + "\n\nThe bot must be an admin of the channel with the <b>Invite users</b> and "
                          "<b>Ban users</b> rights.")
        return
    if len(ctx.args) != 2 or ctx.args[0] not in keys:
        await ctx.reply("Usage: <code>/vipconfig monthly_stars 600</code>")
        return
    key, raw = ctx.args
    try:
        value: int | bool = (raw.lower() in ("1", "true", "yes", "on")) if key == "require_verification" else int(raw)
    except ValueError:
        await ctx.reply("That value must be a number.")
        return
    stored = await tenants_repo.get_global(ctx.session, "vip")
    await tenants_repo.set_global(ctx.session, "vip", {**stored, key: value})
    await tenants_repo.add_audit(ctx.session, "vip_config", ctx.user_id, None, {"key": key})
    await ctx.reply(f"✅ {key} = {value}")
