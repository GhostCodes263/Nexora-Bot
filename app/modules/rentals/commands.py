from __future__ import annotations

from html import escape

from sqlalchemy import select

from app.bot.context import Ctx
from app.bot.keyboards import billing as kb
from app.bot.registry import command
from app.database.models import Payment
from app.services import billing, config, plans
from app.services.gates import plan_of
from app.services.roles import Role

CAT = "rentals"


async def _plans_view(ctx: Ctx, pick=None) -> kb.View:
    cfg = await config.billing(ctx.session)
    rows = await plans.list_plans(ctx.session)
    current = (await plan_of(ctx)).code if ctx.tenant else ""
    if pick is not None:
        rows = pick(rows, current)
    return kb.plans_view(rows, current, int(cfg["trial_days"]))


@command("plans", description="See the available plans and prices", category=CAT)
async def plans_cmd(ctx: Ctx) -> None:
    text, markup = await _plans_view(ctx)
    if ctx.tenant is None:
        await ctx.reply(text + "\n\nOpen /plans inside your group to subscribe it.")
    else:
        await ctx.reply(text, markup)


@command("rent", description="Rent the bot for this group", category=CAT, aliases=("subscribe",),
         permission=Role.ADMIN, scope="group")
async def rent(ctx: Ctx) -> None:
    text, markup = await _plans_view(ctx)
    await ctx.reply(text, markup)


@command("trial", description="Start a free trial of a paid plan", category=CAT, usage="/trial [plan]",
         examples=("/trial pro",), permission=Role.ADMIN, scope="group")
async def trial(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    code = (ctx.args[0].lower() if ctx.args else "pro")
    ok, text = await billing.start_trial(ctx.session, ctx.cache, ctx.tenant, ctx.user_id, code)
    await ctx.reply(text)


@command("subscription", description="Show this group's plan and expiry", category=CAT, scope="group")
async def subscription(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rental = await plans.get_rental(ctx.session, ctx.tenant.id)
    view = await plan_of(ctx)
    await ctx.reply(billing.describe(rental, view))


@command("renew", description="Renew or extend the current plan", category=CAT, permission=Role.ADMIN, scope="group")
async def renew(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    cfg = await config.billing(ctx.session)
    rental = await plans.get_rental(ctx.session, ctx.tenant.id)
    code = rental.plan_code if rental else "starter"
    plan = await plans.get_plan(ctx.session, code)
    if plan is None or plan.price_month_cents <= 0:
        text, markup = await _plans_view(ctx)
    else:
        text, markup = kb.pick_view(plan, int(cfg["trial_days"]))
    await ctx.reply(text, markup)


@command("upgrade", description="Move to a higher plan (unused time is converted)", category=CAT,
         permission=Role.ADMIN, scope="group")
async def upgrade(ctx: Ctx) -> None:
    def higher(rows, current):
        cur = next((p for p in rows if p.code == current), None)
        floor = cur.sort if cur else 0
        return [p for p in rows if p.sort > floor]

    text, markup = await _plans_view(ctx, higher)
    await ctx.reply(text + "\n\nYour unused time is converted into time on the new plan.", markup)


@command("downgrade", description="Move to a cheaper plan (unused time is converted)", category=CAT,
         permission=Role.ADMIN, scope="group")
async def downgrade(ctx: Ctx) -> None:
    def lower(rows, current):
        cur = next((p for p in rows if p.code == current), None)
        ceiling = cur.sort if cur else 99
        return [p for p in rows if p.sort < ceiling]

    text, markup = await _plans_view(ctx, lower)
    await ctx.reply(text + "\n\nYour unused time is converted into extra time on the cheaper plan.", markup)


@command("cancel", description="Stop renewal reminders (access stays until expiry)", category=CAT,
         permission=Role.ADMIN, scope="group")
async def cancel(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rental = await plans.get_rental(ctx.session, ctx.tenant.id)
    if rental is None or rental.status not in plans.ACTIVE_STATES:
        await ctx.reply("There is no active subscription to cancel.")
        return
    rental.cancel_at_period_end = True
    await ctx.reply("🔕 Done. Nothing is auto-charged (Stars are one-time payments); "
                    "reminders are off and paid features stay until the expiry date. Use /renew any time.")


@command("features", description="What your plan includes and what the next one adds", category=CAT, scope="group")
async def features(ctx: Ctx) -> None:
    view = await plan_of(ctx)
    lines = [f"<b>✨ {view.name} plan</b>"]
    lines += [f"✅ {plans.FEATURE_LABELS.get(f, f)}" for f in view.features]
    lines += [f"• up to {v} {plans.LIMIT_LABELS.get(k, k)}" for k, v in view.limits.items()]
    nxt = [p for p in await plans.list_plans(ctx.session) if p.price_month_cents > view.price_month_cents][:1]
    if nxt:
        extra = [f for f in nxt[0].features if f not in view.features]
        if extra:
            lines.append(f"\n<b>{nxt[0].name} adds:</b>")
            lines += [f"➕ {plans.FEATURE_LABELS.get(f, f)}" for f in extra]
    await ctx.reply("\n".join(lines))


@command("billing", description="Payment history and subscription details", category=CAT,
         permission=Role.ADMIN, scope="group")
async def billing_cmd(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    rental = await plans.get_rental(ctx.session, ctx.tenant.id)
    view = await plan_of(ctx)
    lines = [billing.describe(rental, view), "", "<b>🧾 Payments</b>"]
    rows = (await ctx.session.execute(
        select(Payment).where(Payment.tenant_id == ctx.tenant.id).order_by(Payment.id.desc()).limit(10)
    )).scalars().all()
    if not rows:
        lines.append("No payments yet.")
    for p in rows:
        lines.append(f"{p.created_at:%Y-%m-%d} · {escape(p.plan_code or '-')} · "
                     f"{'monthly' if p.period == 'm' else 'yearly'} · {p.amount} {'⭐' if p.currency == 'XTR' else p.currency}"
                     f" · {p.status}")
    await ctx.reply("\n".join(lines))
