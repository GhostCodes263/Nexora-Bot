from __future__ import annotations

import json
import secrets
from datetime import timedelta
from html import escape

from sqlalchemy import func, select

from app.bot.context import Ctx
from app.bot.handlers.billing import broadcast_keyboard
from app.bot.registry import command
from app.database.models import Payment, Plan, Rental, Tenant, TrialHistory
from app.repositories import tenants as tenants_repo
from app.services import config, plans
from app.services import payments as pay
from app.services.roles import Role
from app.utils.time import aware, utcnow

CAT = "payments"


@command("rentals", description="Rentals by status and plan", category=CAT, permission=Role.SUPER_ADMIN, scope="private")
async def rentals(ctx: Ctx) -> None:
    rows = (await ctx.session.execute(
        select(Rental.status, Rental.plan_code, func.count()).group_by(Rental.status, Rental.plan_code)
        .order_by(Rental.status))).all()
    if not rows:
        await ctx.reply("No rentals yet.")
        return
    await ctx.reply("<b>💎 Rentals</b>\n" + "\n".join(f"• {s} / {p}: {n}" for s, p, n in rows))


@command("subscriptions", description="Active and trial subscriptions, soonest expiry first", category=CAT,
         permission=Role.SUPER_ADMIN, scope="private")
async def subscriptions(ctx: Ctx) -> None:
    rows = (await ctx.session.execute(
        select(Rental, Tenant).join(Tenant, Tenant.id == Rental.tenant_id)
        .where(Rental.status.in_(plans.ACTIVE_STATES)).order_by(Rental.expires_at).limit(15))).all()
    if not rows:
        await ctx.reply("No active subscriptions.")
        return
    lines = ["<b>📅 Subscriptions</b>"]
    for r, t in rows:
        lines.append(f"• {escape(t.title or str(t.chat_id))} — {r.plan_code} ({r.status}) until {aware(r.expires_at):%Y-%m-%d}")
    await ctx.reply("\n".join(lines))


@command("revenue", description="Revenue in Telegram Stars", category=CAT, usage="/revenue [days]",
         permission=Role.SUPER_ADMIN, scope="private")
async def revenue(ctx: Ctx) -> None:
    days = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 30
    since = utcnow() - timedelta(days=days)
    rows = (await ctx.session.execute(
        select(Payment.product, func.count(), func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status == "paid", Payment.created_at >= since).group_by(Payment.product))).all()
    refunded = int((await ctx.session.execute(
        select(func.count()).select_from(Payment).where(Payment.status == "refunded", Payment.created_at >= since)
    )).scalar_one())
    lines = [f"<b>💰 Revenue, last {days} days</b>"]
    total = 0
    for product, n, amount in rows:
        lines.append(f"• {product}: {n} payments · {amount} ⭐")
        total += int(amount)
    lines.append(f"<b>Total: {total} ⭐</b> · refunds: {refunded}")
    lines.append("<i>Stars are converted to money when you withdraw them from Telegram.</i>")
    await ctx.reply("\n".join(lines))


@command("payments", description="Latest payments", category=CAT, permission=Role.SUPER_ADMIN, scope="private")
async def payments_cmd(ctx: Ctx) -> None:
    rows = (await ctx.session.execute(select(Payment).order_by(Payment.id.desc()).limit(10))).scalars().all()
    if not rows:
        await ctx.reply("No payments yet.")
        return
    lines = ["<b>🧾 Latest payments</b> (id · user · product · amount)"]
    for p in rows:
        lines.append(f"#{p.id} · <code>{p.user_id}</code> · {p.product}/{p.plan_code or '-'} · {p.amount}⭐ · {p.status}")
    await ctx.reply("\n".join(lines))


@command("refund", description="Refund a Stars payment and reverse the purchase", category=CAT,
         usage="/refund <payment id>", permission=Role.OWNER, scope="private")
async def refund(ctx: Ctx) -> None:
    if not ctx.args or not ctx.args[0].lstrip("#").isdigit():
        await ctx.reply("Usage: <code>/refund 12</code> (the # from /payments)")
        return
    payment = await ctx.session.get(Payment, int(ctx.args[0].lstrip("#")))
    if payment is None:
        await ctx.reply("No such payment.")
        return
    ok, text = await pay.refund_payment(ctx.session, ctx.bot, ctx.cache, payment, ctx.settings)
    await ctx.reply(("✅ " if ok else "⚠️ ") + escape(text))


@command("setplan", description="Edit a plan's price, Stars price or availability", category=CAT,
         usage="/setplan <code> <price_month|price_year|stars_month|stars_year|active> <value>",
         examples=("/setplan starter price_month 4.99", "/setplan pro stars_month 500"),
         permission=Role.OWNER, scope="private")
async def setplan(ctx: Ctx) -> None:
    a = ctx.args
    plan = await ctx.session.get(Plan, a[0].lower()) if a else None
    if plan is None or len(a) != 3:
        await ctx.reply("Usage: <code>/setplan pro stars_month 500</code>")
        return
    field, raw = a[1].lower(), a[2]
    try:
        if field in ("price_month", "price_year"):
            setattr(plan, f"{field}_cents", round(float(raw) * 100))
        elif field in ("stars_month", "stars_year"):
            setattr(plan, field, int(raw))
        elif field == "active":
            plan.is_active = raw.lower() in ("1", "true", "yes", "on")
        else:
            raise ValueError
    except ValueError:
        await ctx.reply("Unknown field or invalid value.")
        return
    await tenants_repo.add_audit(ctx.session, "plan_edited", ctx.user_id, None, {"plan": plan.code, "field": field, "value": raw})
    await ctx.reply(f"✅ {plan.name}: {field} = {raw}. (Active invoices keep their old amount and will be rejected.)")


@command("setbilling", description="Edit trial/grace/reminder settings", category=CAT,
         usage="/setbilling <trial_days|grace_days|trials_per_user> <number>", permission=Role.OWNER, scope="private")
async def setbilling(ctx: Ctx) -> None:
    cfg = await config.billing(ctx.session)
    if not ctx.args:
        await ctx.reply("<b>Billing settings</b>\n" + "\n".join(f"• {k}: <code>{v}</code>" for k, v in cfg.items()))
        return
    if len(ctx.args) != 2 or ctx.args[0] not in ("trial_days", "grace_days", "trials_per_user") or not ctx.args[1].isdigit():
        await ctx.reply("Usage: <code>/setbilling grace_days 3</code>")
        return
    stored = await tenants_repo.get_global(ctx.session, "billing")
    await tenants_repo.set_global(ctx.session, "billing", {**stored, ctx.args[0]: int(ctx.args[1])})
    await ctx.reply(f"✅ {ctx.args[0]} = {ctx.args[1]}")


@command("broadcast", description="Message all groups or all users (asks to confirm)", category=CAT,
         usage="/broadcast <groups|users> <text>", permission=Role.OWNER, scope="private", cooldown=30)
async def broadcast(ctx: Ctx) -> None:
    target, _, text = ctx.raw_args.partition(" ")
    text = text.strip()
    if target not in ("groups", "users") or not text:
        await ctx.reply("Usage: <code>/broadcast groups Maintenance tonight at 22:00</code>")
        return
    bid = secrets.token_hex(4)
    await ctx.cache.set(f"bc:{bid}", json.dumps({"target": target, "text": text}), 600)
    await ctx.reply(f"<b>⚠️ Confirm broadcast to all {target}</b>\n\n{escape(text)}", broadcast_keyboard(bid))


@command("trials", description="Trial statistics", category=CAT, permission=Role.SUPER_ADMIN, scope="private")
async def trials(ctx: Ctx) -> None:
    n = int((await ctx.session.execute(select(func.count()).select_from(TrialHistory))).scalar_one())
    users = int((await ctx.session.execute(select(func.count(func.distinct(TrialHistory.customer_id))))).scalar_one())
    active = int((await ctx.session.execute(
        select(func.count()).select_from(Rental).where(Rental.status == "trial"))).scalar_one())
    await ctx.reply(f"🎁 Trials started: <b>{n}</b> by <b>{users}</b> users · running now: <b>{active}</b>")
