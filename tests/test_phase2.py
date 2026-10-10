from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers.automod import matches_filter
from app.bot.loader import load_modules
from app.bot.registry import REGISTRY
from app.config.settings import Settings
from app.database.models import Plan
from app.modules.moderation import service as mod
from app.repositories import tenants as tenants_repo
from app.repositories import verification as vrepo
from app.services import billing, payments as pay, plans, vip
from app.services.cache import Cache
from app.services.roles import Role
from app.utils.time import utcnow


@pytest.fixture
async def seeded(session):
    base = ["moderation_basic"]
    starter = base + ["welcome", "captcha", "automod_basic", "auto_replies", "custom_commands", "scheduled_messages"]
    session.add_all([
        Plan(code="free", name="Free", features=base, limits={"filters": 5}, sort=0),
        Plan(code="starter", name="Starter", price_month_cents=399, price_year_cents=3999, stars_month=200,
             stars_year=2000, features=starter, limits={"filters": 25}, sort=1),
        Plan(code="pro", name="Pro", price_month_cents=899, price_year_cents=8999, stars_month=450,
             stars_year=4500, features=starter + ["automod_advanced"], limits={"filters": 100}, sort=2),
    ])
    await session.flush()
    return session


async def _tenant(session, chat_id=-1):
    return await tenants_repo.get_or_create(session, chat_id, "supergroup", f"T{chat_id}")


# --- moderation -----------------------------------------------------------------
def test_parse_duration_bounds():
    assert mod.parse_duration("30m") == timedelta(minutes=30)
    assert mod.parse_duration("2h") == timedelta(hours=2)
    assert mod.parse_duration("1w") == timedelta(days=7)
    assert mod.parse_duration("10s") is None      # below Telegram's useful minimum
    assert mod.parse_duration("400d") is None     # above 366 days means "forever" in Telegram
    assert mod.parse_duration("abc") is None


def test_filter_matching_is_whole_word():
    assert matches_filter("this is spam!", ["spam"])
    assert not matches_filter("spamming is fine", ["spam"])
    assert matches_filter("Buy NOW", ["buy now"])


async def test_warning_thresholds_per_tenant(session):
    bot = AsyncMock()
    a, b = await _tenant(session, -1), await _tenant(session, -2)
    cfg = mod.mod_cfg(a)
    cfg["warn_actions"] = {"2": "kick"}
    mod.save_mod_cfg(a, cfg)
    assert (await mod.apply_warn(session, bot, a, 7, 1, "x"))[1] is None
    count, action = await mod.apply_warn(session, bot, a, 7, 1, "y")
    assert (count, action) == (2, "kick")
    bot.ban_chat_member.assert_awaited()
    # tenant B has its own defaults (mute at 3) and its own warning counts
    assert await mod.warning_count(session, b.id, 7) == 0
    for _ in range(2):
        await mod.apply_warn(session, bot, b, 7, 1, "z")
    count, action = await mod.apply_warn(session, bot, b, 7, 1, "z")
    assert (count, action) == (3, "mute")


# --- plans / trials / rentals --------------------------------------------------------
async def test_free_plan_until_a_rental_exists(seeded):
    t = await _tenant(seeded)
    view = await plans.effective_view(seeded, Cache(""), t)
    assert view.code == "free" and not view.has("welcome")


async def test_trial_once_per_tenant_and_limit_per_user(seeded):
    cache = Cache("")
    t1, t2, t3 = [await _tenant(seeded, -i) for i in (1, 2, 3)]
    ok, _ = await billing.start_trial(seeded, cache, t1, 5, "pro")
    assert ok
    ok, msg = await billing.start_trial(seeded, cache, t1, 6, "pro")
    assert not ok and "already used" in msg
    assert (await billing.start_trial(seeded, cache, t2, 5, "pro"))[0]
    ok, msg = await billing.start_trial(seeded, cache, t3, 5, "pro")
    assert not ok and "maximum" in msg
    assert (await plans.effective_view(seeded, cache, t1)).code == "pro"


async def test_cannot_trial_the_free_plan(seeded):
    ok, _ = await billing.start_trial(seeded, Cache(""), await _tenant(seeded), 5, "free")
    assert not ok


async def test_paid_period_extends_and_plan_change_converts_time(seeded):
    cache = Cache("")
    t = await _tenant(seeded)
    r = await billing.activate_paid(seeded, cache, t, 5, "starter", "m")
    first = r.expires_at
    r = await billing.activate_paid(seeded, cache, t, 5, "starter", "m")
    assert (r.expires_at - first).days == 30                 # same plan: time is added
    left = (r.expires_at - utcnow()).total_seconds()
    r = await billing.activate_paid(seeded, cache, t, 5, "pro", "m")
    # remaining Starter value is converted at the price ratio and added to the new period
    expected = 30 * 86400 + left * 399 / 899
    assert abs((r.expires_at - utcnow()).total_seconds() - expected) < 5
    assert r.plan_code == "pro" and r.status == "active"


async def test_expiry_grace_then_expired_keeps_data(seeded):
    cache = Cache("")
    bot = AsyncMock()
    t = await _tenant(seeded)
    r = await billing.activate_paid(seeded, cache, t, 5, "starter", "m")
    r.expires_at = utcnow() - timedelta(minutes=1)
    await billing.process_expiries(seeded, bot, cache)
    assert r.status == "grace" and r.grace_until is not None
    assert (await plans.effective_view(seeded, cache, t)).code == "starter"   # still on during grace
    bot.send_message.assert_awaited()
    r.grace_until = utcnow() - timedelta(minutes=1)
    await billing.process_expiries(seeded, bot, cache)
    assert r.status == "expired"
    assert (await plans.effective_view(seeded, cache, t)).code == "free"      # paid features off
    assert await tenants_repo.get_by_chat_id(seeded, t.chat_id) is not None   # tenant data not deleted
    # renewing restores everything
    r = await billing.activate_paid(seeded, cache, t, 5, "starter", "m")
    assert r.status == "active"
    assert (await plans.effective_view(seeded, cache, t)).code == "starter"


async def test_trial_expires_without_grace(seeded):
    cache = Cache("")
    t = await _tenant(seeded)
    await billing.start_trial(seeded, cache, t, 5, "pro")
    r = await plans.get_rental(seeded, t.id)
    r.expires_at = utcnow() - timedelta(minutes=1)
    await billing.process_expiries(seeded, AsyncMock(), cache)
    assert r.status == "expired"


# --- payments --------------------------------------------------------------------------------
def _sp(charge="ch1", amount=450, payload="rent:1:pro:m"):
    return SimpleNamespace(total_amount=amount, currency="XTR", invoice_payload=payload,
                           telegram_payment_charge_id=charge, provider_payment_charge_id="p1")


def test_payload_roundtrip():
    assert pay.parse_payload(pay.rent_payload(3, "pro", "y")) == {"kind": "rent", "tenant_id": 3, "plan": "pro", "period": "y"}
    assert pay.parse_payload("vip:m") == {"kind": "vip", "period": "m"}
    for bad in ("", "rent:x:pro:m", "rent:1:pro:z", "other:1", "vip:q"):
        assert pay.parse_payload(bad) is None


async def test_duplicate_payment_callback_is_ignored(seeded):
    parsed = {"kind": "rent", "tenant_id": 1, "plan": "pro", "period": "m"}
    first = await pay.record_payment(seeded, 5, parsed, _sp())
    second = await pay.record_payment(seeded, 5, parsed, _sp())
    assert first is not None and second is None


async def test_checkout_validation_uses_database_prices(seeded):
    t = await _tenant(seeded)
    payload = pay.rent_payload(t.id, "pro", "m")
    assert await pay.validate_checkout(seeded, payload, 450, "XTR", 5) is None
    assert await pay.validate_checkout(seeded, payload, 1, "XTR", 5) is not None      # tampered amount
    assert await pay.validate_checkout(seeded, payload, 450, "USD", 5) is not None
    assert await pay.validate_checkout(seeded, pay.rent_payload(t.id, "free", "m"), 0, "XTR", 5) is not None
    assert await pay.validate_checkout(seeded, "garbage", 450, "XTR", 5) is not None


async def test_refund_reverses_the_purchase(seeded):
    cache, bot = Cache(""), AsyncMock()
    t = await _tenant(seeded)
    await billing.activate_paid(seeded, cache, t, 5, "starter", "m")
    parsed = {"kind": "rent", "tenant_id": t.id, "plan": "starter", "period": "m"}
    p = await pay.record_payment(seeded, 5, parsed, _sp("ch9", 200, pay.rent_payload(t.id, "starter", "m")))
    ok, _ = await pay.refund_payment(seeded, bot, cache, p, Settings())
    assert ok and p.status == "refunded"
    bot.refund_star_payment.assert_awaited()
    assert (await plans.get_rental(seeded, t.id)).status == "expired"
    ok, _ = await pay.refund_payment(seeded, bot, cache, p, Settings())
    assert not ok                                                                    # no double refunds


# --- verification --------------------------------------------------------------------------------
async def test_verification_workflow_and_audit_trail(session):
    app = await vrepo.create(session, 42, {"name": "A", "age": "30"}, None)
    assert app.public_id.startswith("V-") and len(app.public_id) == 7 and app.status == "PENDING"
    assert not await vrepo.is_approved(session, 42)
    await vrepo.set_status(session, app, "UNDER_REVIEW", 1)
    await vrepo.set_status(session, app, "APPROVED", 1)
    assert await vrepo.is_approved(session, 42)
    assert (await vrepo.get_by_public_id(session, app.public_id.lower())).id == app.id
    from sqlalchemy import select

    from app.database.models import VerificationAction
    actions = (await session.execute(select(VerificationAction.action))).scalars().all()
    assert actions == ["SUBMITTED", "UNDER_REVIEW", "APPROVED"]


async def test_reviewers_get_no_elevated_role(session):
    await vrepo.add_reviewer(session, 77, 1)
    assert await vrepo.is_reviewer(session, 77, Role.USER)
    assert not await vrepo.is_reviewer(session, 78, Role.ADMIN)      # even a group admin is not a reviewer
    assert await vrepo.is_reviewer(session, 1, Role.OWNER)           # the owner always is
    from app.services.permissions import resolve_role
    role = await resolve_role(session, AsyncMock(), Cache(""), Settings(owner_id=1), 77, None)
    assert role == Role.USER


# --- VIP --------------------------------------------------------------------------------------------
async def test_vip_extension_grace_and_revocation(session):
    bot = AsyncMock()
    st = Settings(vip_channel_id=-1001)
    m = await vip.activate(session, 9, "m")
    first = m.expires_at
    m = await vip.activate(session, 9, "m")
    assert (m.expires_at - first).days == 30
    m.expires_at = utcnow() - timedelta(minutes=1)
    await vip.process_expiries(session, bot, st)
    assert m.status == "grace" and vip.is_active(m)
    bot.ban_chat_member.assert_not_awaited()
    m.grace_until = utcnow() - timedelta(minutes=1)
    await vip.process_expiries(session, bot, st)
    assert m.status == "expired" and not vip.is_active(m)
    bot.ban_chat_member.assert_awaited()                              # access removed when it ends


async def test_vip_requires_verification(session):
    assert await vip.eligibility_error(session, 5) is not None
    app = await vrepo.create(session, 5, {}, None)
    await vrepo.set_status(session, app, "APPROVED", 1)
    assert await vip.eligibility_error(session, 5) is None


# --- registry -----------------------------------------------------------------------------------------
def test_registry_has_phase2_commands_with_correct_permissions():
    load_modules()
    assert len(REGISTRY.all()) >= 120
    for name in ("ban", "warn", "plans", "trial", "verify", "vip", "refund", "setwelcome", "addcmd"):
        assert REGISTRY.get(name) is not None, name
    assert REGISTRY.get("refund").permission == Role.OWNER
    assert REGISTRY.get("ban").permission >= Role.ADMIN
    assert REGISTRY.get("verify").permission == Role.USER
