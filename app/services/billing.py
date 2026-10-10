from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Rental, Tenant, TrialHistory
from app.repositories import tenants as tenants_repo
from app.services import config, plans
from app.services.cache import Cache
from app.utils.time import aware, utcnow

log = logging.getLogger(__name__)

PERIOD_DAYS = {"m": 30, "y": 365}


async def start_trial(
    session: AsyncSession, cache: Cache, tenant: Tenant, user_id: int, plan_code: str
) -> tuple[bool, str]:
    cfg = await config.billing(session)
    plan = await plans.get_plan(session, plan_code)
    if plan is None or not plan.is_active or plan.price_month_cents <= 0:
        return False, "Pick a paid plan, for example <code>/trial pro</code>."
    used = await session.execute(select(TrialHistory.id).where(TrialHistory.tenant_id == tenant.id))
    if used.scalars().first() is not None:
        return False, "This group has already used its free trial."
    count = int((await session.execute(
        select(func.count()).select_from(TrialHistory).where(TrialHistory.customer_id == user_id)
    )).scalar_one())
    if count >= int(cfg["trials_per_user"]):
        return False, "You have reached the maximum number of free trials."
    now = utcnow()
    rental = await plans.get_rental(session, tenant.id)
    if rental is not None and rental.status in ("active", "grace") and not rental.is_trial:
        return False, "This group already has a paid subscription."
    try:
        async with session.begin_nested():
            session.add(TrialHistory(tenant_id=tenant.id, customer_id=user_id, plan_code=plan_code))
            await session.flush()
    except IntegrityError:
        return False, "This group has already used its free trial."
    expires = now + timedelta(days=int(cfg["trial_days"]))
    if rental is None:
        session.add(Rental(
            tenant_id=tenant.id, customer_id=user_id, plan_code=plan_code, status="trial", is_trial=True,
            payment_status="none", started_at=now, expires_at=expires, reminders=[],
        ))
    else:
        rental.customer_id = user_id
        rental.plan_code = plan_code
        rental.status = "trial"
        rental.is_trial = True
        rental.started_at = now
        rental.expires_at = expires
        rental.grace_until = None
        rental.reminders = []
    await tenants_repo.add_audit(session, "trial_started", user_id, tenant.id, {"plan": plan_code})
    await plans.invalidate(cache, tenant.id)
    return True, (f"🎁 <b>{plan.name}</b> trial started: {cfg['trial_days']} days, ends {expires:%Y-%m-%d}.")


async def activate_paid(
    session: AsyncSession, cache: Cache, tenant: Tenant, customer_id: int, plan_code: str, period: str
) -> Rental:
    """Apply a paid period. Switching plans converts the unused time into the new plan's time."""
    plan = await plans.get_plan(session, plan_code)
    assert plan is not None
    now = utcnow()
    add = timedelta(days=PERIOD_DAYS[period])
    rental = await plans.get_rental(session, tenant.id)
    new_exp = now + add
    if rental is not None and rental.status in plans.ACTIVE_STATES and aware(rental.expires_at) > now:
        remaining = aware(rental.expires_at) - now
        if rental.plan_code == plan_code and not rental.is_trial:
            new_exp = aware(rental.expires_at) + add
        elif not rental.is_trial:
            old = await plans.get_plan(session, rental.plan_code)
            if old is not None and old.price_month_cents > 0 and plan.price_month_cents > 0:
                new_exp = now + add + remaining * (old.price_month_cents / plan.price_month_cents)
    if rental is None:
        rental = Rental(tenant_id=tenant.id, customer_id=customer_id, plan_code=plan_code, status="active",
                        is_trial=False, payment_status="paid", started_at=now, expires_at=new_exp, reminders=[])
        session.add(rental)
    else:
        rental.customer_id = customer_id
        rental.plan_code = plan_code
        rental.status = "active"
        rental.is_trial = False
        rental.payment_status = "paid"
        rental.expires_at = new_exp
        rental.grace_until = None
        rental.cancel_at_period_end = False
        rental.reminders = []
    await plans.invalidate(cache, tenant.id)
    return rental


def describe(rental: Rental | None, plan: plans.PlanView, now=None) -> str:
    now = now or utcnow()
    if rental is None or rental.status == "expired":
        extra = "\nYour previous subscription expired. Data is kept; renew to restore paid features." if rental else ""
        return f"💎 Plan: <b>{plan.name}</b> (no active subscription){extra}"
    exp = aware(rental.expires_at)
    left = exp - now
    lines = [f"💎 Plan: <b>{plan.name}</b>", f"Status: <b>{rental.status}</b>{' (trial)' if rental.is_trial else ''}"]
    if rental.status == "grace":
        lines.append(f"⚠️ Expired {exp:%Y-%m-%d}. Grace period ends {aware(rental.grace_until):%Y-%m-%d}.")
    else:
        lines.append(f"Expires: {exp:%Y-%m-%d} ({max(left.days, 0)} days left)")
    if rental.cancel_at_period_end:
        lines.append("🔕 Renewal reminders are off (cancelled).")
    return "\n".join(lines)


async def _notify(bot: Bot, tenant: Tenant | None, customer_id: int, text: str, kb=None) -> None:
    for chat_id, markup in ((customer_id, None), (tenant.chat_id if tenant else None, kb)):
        if chat_id is None:
            continue
        try:
            await bot.send_message(chat_id, text, reply_markup=markup)
        except TelegramAPIError as exc:
            log.info("notify failed for %s: %s", chat_id, exc)


async def process_expiries(session: AsyncSession, bot: Bot, cache: Cache) -> None:
    from app.bot.keyboards.billing import plans_button

    cfg = await config.billing(session)
    now = utcnow()
    grace = timedelta(days=int(cfg["grace_days"]))
    rows = (await session.execute(
        select(Rental).where(Rental.status.in_(plans.ACTIVE_STATES))
    )).scalars().all()
    for r in rows:
        tenant = await session.get(Tenant, r.tenant_id)
        title = tenant.title if tenant else "your group"
        exp = aware(r.expires_at)
        if r.status in ("trial", "active") and exp <= now:
            if r.status == "trial" or int(cfg["grace_days"]) <= 0:
                r.status = "expired"
                text = (f"⌛ The {'trial' if r.is_trial else 'subscription'} for <b>{title}</b> has ended. "
                        "Paid features are off; your data is kept. Subscribe to restore everything.")
            else:
                r.status = "grace"
                r.grace_until = exp + grace
                text = (f"⌛ The subscription for <b>{title}</b> expired. Paid features stay on until "
                        f"{aware(r.grace_until):%Y-%m-%d}. Renew to keep them.")
            await plans.invalidate(cache, r.tenant_id)
            await tenants_repo.add_audit(session, "rental_" + r.status, None, r.tenant_id, {})
            await _notify(bot, tenant, r.customer_id, text, plans_button())
        elif r.status == "grace" and r.grace_until is not None and aware(r.grace_until) <= now:
            r.status = "expired"
            await plans.invalidate(cache, r.tenant_id)
            await tenants_repo.add_audit(session, "rental_expired", None, r.tenant_id, {})
            await _notify(bot, tenant, r.customer_id,
                          f"⛔ The grace period for <b>{title}</b> ended. Paid features are now off. "
                          "Your data is kept and returns as soon as you renew.", plans_button())
        elif r.status in ("trial", "active") and not r.cancel_at_period_end:
            marks = list(r.reminders or [])
            fresh = False
            for d in cfg["reminder_days"]:
                key = f"d{d}"
                if exp - now <= timedelta(days=int(d)) and key not in marks:
                    marks.append(key)
                    fresh = True
            if fresh:
                r.reminders = marks
                days_left = max((exp - now).days, 0)
                await _notify(bot, tenant, r.customer_id,
                              f"⏰ <b>{title}</b>: your {'trial' if r.is_trial else 'subscription'} ends in "
                              f"about {days_left} day(s). Renew to avoid interruption.", plans_button())
