from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LabeledPrice, SuccessfulPayment
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Payment, Tenant
from app.repositories import tenants as tenants_repo
from app.repositories import verification as verification_repo
from app.services import billing, config, plans
from app.services import vip as vip_service
from app.services.cache import Cache
from app.utils.time import aware, utcnow

log = logging.getLogger(__name__)


class PaymentProvider(ABC):
    """Add a new provider by subclassing and registering it in PROVIDERS."""

    name: str
    currency: str

    @abstractmethod
    async def send_invoice(
        self, bot: Bot, chat_id: int, title: str, description: str, payload: str, amount: int
    ) -> None: ...

    @abstractmethod
    async def refund(self, bot: Bot, payment: Payment) -> None: ...


class TelegramStarsProvider(PaymentProvider):
    name = "telegram_stars"
    currency = "XTR"

    async def send_invoice(
        self, bot: Bot, chat_id: int, title: str, description: str, payload: str, amount: int
    ) -> None:
        await bot.send_invoice(
            chat_id=chat_id,
            title=title[:32],
            description=description[:255] or title[:32],
            payload=payload,
            currency=self.currency,
            prices=[LabeledPrice(label=title[:32], amount=amount)],
            provider_token="",  # Stars need no provider token
        )

    async def refund(self, bot: Bot, payment: Payment) -> None:
        await bot.refund_star_payment(
            user_id=payment.user_id, telegram_payment_charge_id=payment.telegram_charge_id
        )


PROVIDERS: dict[str, PaymentProvider] = {"telegram_stars": TelegramStarsProvider()}


def get_provider(name: str = "telegram_stars") -> PaymentProvider:
    return PROVIDERS[name]


# ---- payload format: "rent:<tenant_id>:<plan>:<m|y>"  or  "vip:<m|y>" -------------------------

def rent_payload(tenant_id: int, plan: str, period: str) -> str:
    return f"rent:{tenant_id}:{plan}:{period}"


def vip_payload(period: str) -> str:
    return f"vip:{period}"


def parse_payload(payload: str) -> dict | None:
    parts = payload.split(":")
    try:
        if parts[0] == "rent" and len(parts) == 4 and parts[3] in ("m", "y"):
            return {"kind": "rent", "tenant_id": int(parts[1]), "plan": parts[2], "period": parts[3]}
        if parts[0] == "vip" and len(parts) == 2 and parts[1] in ("m", "y"):
            return {"kind": "vip", "period": parts[1]}
    except ValueError:
        return None
    return None


async def expected_amount(session: AsyncSession, parsed: dict) -> int | None:
    """What this payload SHOULD cost, from the database. The client's amount is never trusted."""
    if parsed["kind"] == "rent":
        plan = await plans.get_plan(session, parsed["plan"])
        if plan is None or not plan.is_active or plan.price_month_cents <= 0:
            return None
        return plan.stars_month if parsed["period"] == "m" else plan.stars_year
    cfg = await config.vip(session)
    return int(cfg["monthly_stars"] if parsed["period"] == "m" else cfg["annual_stars"])


async def validate_checkout(
    session: AsyncSession, payload: str, amount: int, currency: str, user_id: int = 0
) -> str | None:
    """Return an error message, or None if the checkout is valid."""
    parsed = parse_payload(payload)
    if parsed is None:
        return "This invoice is not valid anymore."
    if currency != "XTR":
        return "Unsupported currency."
    if parsed["kind"] == "rent" and await session.get(Tenant, parsed["tenant_id"]) is None:
        return "That group no longer exists."
    if parsed["kind"] == "vip":
        cfg = await config.vip(session)
        if cfg["require_verification"] and not await verification_repo.is_approved(session, user_id):
            return "VIP requires an approved verification. Use /verify first."
    expected = await expected_amount(session, parsed)
    if expected is None or expected != amount:
        return "The price changed. Please request a new invoice."
    return None


async def record_payment(
    session: AsyncSession, user_id: int, parsed: dict, sp: SuccessfulPayment
) -> Payment | None:
    """Store the payment. Returns None if this charge was already processed (idempotency)."""
    existing = await session.execute(
        select(Payment.id).where(Payment.telegram_charge_id == sp.telegram_payment_charge_id)
    )
    if existing.scalars().first() is not None:
        return None
    payment = Payment(
        user_id=user_id,
        product=parsed["kind"],
        tenant_id=parsed.get("tenant_id"),
        plan_code=parsed.get("plan"),
        period=parsed["period"],
        amount=sp.total_amount,
        currency=sp.currency,
        provider="telegram_stars",
        telegram_charge_id=sp.telegram_payment_charge_id,
        provider_charge_id=sp.provider_payment_charge_id or None,
        payload=sp.invoice_payload,
        status="paid",
    )
    try:
        async with session.begin_nested():
            session.add(payment)
            await session.flush()
    except IntegrityError:
        return None  # a concurrent duplicate callback won the race
    return payment


async def fulfill(session: AsyncSession, bot: Bot, cache: Cache, payment: Payment, parsed: dict, settings) -> str:
    """Grant what was paid for. Returns the confirmation text for the buyer."""
    if parsed["kind"] == "rent":
        tenant = await session.get(Tenant, parsed["tenant_id"])
        assert tenant is not None
        rental = await billing.activate_paid(session, cache, tenant, payment.user_id, parsed["plan"], parsed["period"])
        await tenants_repo.add_audit(session, "rental_paid", payment.user_id, tenant.id,
                                     {"plan": parsed["plan"], "period": parsed["period"], "payment": payment.id})
        plan = await plans.get_plan(session, parsed["plan"])
        return (f"✅ <b>Payment received!</b> {plan.name if plan else parsed['plan']} is active for "
                f"<b>{tenant.title or 'your group'}</b> until {aware(rental.expires_at):%Y-%m-%d}.")
    m = await vip_service.activate(session, payment.user_id, parsed["period"])
    await tenants_repo.add_audit(session, "vip_paid", payment.user_id, None, {"payment": payment.id})
    link = None
    ch = await vip_service.channel_id(session, settings)
    if ch:
        link = await vip_service.invite_link(bot, ch)
    text = f"✅ <b>VIP unlocked</b> until {aware(m.expires_at):%Y-%m-%d}."
    if link:
        text += f"\nRequest access here (approved automatically): {link}"
    elif not ch:
        text += "\nThe VIP channel isn't configured yet; the owner will contact you."
    return text


async def refund_payment(session: AsyncSession, bot: Bot, cache: Cache, payment: Payment, settings) -> tuple[bool, str]:
    if payment.status == "refunded":
        return False, "Already refunded."
    try:
        await get_provider(payment.provider).refund(bot, payment)
    except TelegramAPIError as exc:
        return False, f"Telegram refused the refund: {exc}"
    payment.status = "refunded"
    payment.refunded_at = utcnow()
    days = 30 if payment.period == "m" else 365
    now = utcnow()
    if payment.product == "rent" and payment.tenant_id:
        rental = await plans.get_rental(session, payment.tenant_id)
        if rental is not None:
            new_exp = aware(rental.expires_at) - timedelta(days=days)
            rental.expires_at = new_exp
            if new_exp <= now:
                rental.status = "expired"
                rental.payment_status = "refunded"
            await plans.invalidate(cache, payment.tenant_id)
    elif payment.product == "vip":
        m = await vip_service.get(session, payment.user_id)
        if m is not None:
            m.expires_at = aware(m.expires_at) - timedelta(days=days)
            if m.expires_at <= now:
                m.status = "expired"
                ch = await vip_service.channel_id(session, settings)
                if ch:
                    await vip_service.revoke_access(bot, ch, payment.user_id)
    await tenants_repo.add_audit(session, "payment_refunded", None, payment.tenant_id, {"payment": payment.id})
    return True, "Refunded and the purchase was reversed."
