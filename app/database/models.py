from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "tp_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # Telegram ID
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), default="")
    is_globally_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlobalRole(Base):
    """Platform-level staff (developer / super admin). The OWNER comes from OWNER_ID only."""

    __tablename__ = "tp_global_roles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    role: Mapped[int] = mapped_column(Integer)
    granted_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tenant(Base):
    """One row per group / supergroup / channel the bot serves. Everything else hangs off this."""

    __tablename__ = "tp_tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    chat_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256), default="")
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    prefix: Mapped[str] = mapped_column(String(4), default="/")
    # None = use each module's default; otherwise explicit {"module": bool} overrides.
    module_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TenantRole(Base):
    __tablename__ = "tp_tenant_roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tp_tenant_role_user"),
        Index("ix_tp_tenant_roles_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tp_tenants.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[int] = mapped_column(Integer)
    granted_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "tp_audit_logs"
    __table_args__ = (Index("ix_tp_audit_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tp_tenants.id", ondelete="SET NULL"))
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GlobalSetting(Base):
    """Key/value platform settings (maintenance mode etc.), editable by the owner."""

    __tablename__ = "tp_global_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


# =============================== Phase 2 ====================================

def _fk_tenant(ondelete: str = "CASCADE"):
    return ForeignKey("tp_tenants.id", ondelete=ondelete)


class ModWarning(Base):
    __tablename__ = "tp_warnings"
    __table_args__ = (Index("ix_tp_warnings_tenant_user", "tenant_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    moderator_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModAction(Base):
    __tablename__ = "tp_mod_actions"
    __table_args__ = (Index("ix_tp_mod_actions_tenant_target", "tenant_id", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    action: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[int] = mapped_column(BigInteger)
    moderator_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(256), default="")
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModNote(Base):
    __tablename__ = "tp_mod_notes"
    __table_args__ = (Index("ix_tp_mod_notes_tenant_user", "tenant_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomCommand(Base):
    __tablename__ = "tp_custom_commands"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tp_custom_cmd"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    name: Mapped[str] = mapped_column(String(32))
    response: Mapped[str] = mapped_column(String(2000))


class Trigger(Base):
    __tablename__ = "tp_triggers"
    __table_args__ = (UniqueConstraint("tenant_id", "keyword", name="uq_tp_trigger"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    keyword: Mapped[str] = mapped_column(String(64))
    response: Mapped[str] = mapped_column(String(2000))


class ScheduledMessage(Base):
    __tablename__ = "tp_scheduled_messages"
    __table_args__ = (Index("ix_tp_sched_due", "sent", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    chat_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(String(3500))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class MemberEvent(Base):
    __tablename__ = "tp_member_events"
    __table_args__ = (Index("ix_tp_member_events_tenant_time", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))  # join | leave
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaptchaPending(Base):
    __tablename__ = "tp_captcha_pending"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tp_captcha_user"),
        Index("ix_tp_captcha_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Plan(Base):
    __tablename__ = "tp_plans"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    price_month_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_year_cents: Mapped[int] = mapped_column(Integer, default=0)
    stars_month: Mapped[int] = mapped_column(Integer, default=0)
    stars_year: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[list] = mapped_column(JSON, default=list)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Rental(Base):
    """One rental per tenant. Status: trial | active | grace | expired."""

    __tablename__ = "tp_rentals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant(), unique=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_code: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12))
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_status: Mapped[str] = mapped_column(String(12), default="none")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    reminders: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrialHistory(Base):
    __tablename__ = "tp_trial_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant(), unique=True)  # one trial per tenant, ever
    customer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_code: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "tp_payments"
    __table_args__ = (
        UniqueConstraint("telegram_charge_id", name="uq_tp_payment_charge"),
        Index("ix_tp_payments_user", "user_id"),
        Index("ix_tp_payments_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    product: Mapped[str] = mapped_column(String(8))  # rent | vip
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tp_tenants.id", ondelete="SET NULL"))
    plan_code: Mapped[str | None] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(2))  # m | y
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(24))
    telegram_charge_id: Mapped[str] = mapped_column(String(128))
    provider_charge_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(12), default="paid")  # paid | refunded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationApplication(Base):
    __tablename__ = "tp_verification_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(12), unique=True)  # e.g. V-48291
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    photo_file_id: Mapped[str | None] = mapped_column(String(256))
    reviewer_note: Mapped[str | None] = mapped_column(String(500))
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerificationAction(Base):
    __tablename__ = "tp_verification_actions"
    __table_args__ = (Index("ix_tp_verif_actions_app", "application_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("tp_verification_applications.id", ondelete="CASCADE")
    )
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerificationReviewer(Base):
    """Trusted reviewers. Being a reviewer grants NO platform or tenant role."""

    __tablename__ = "tp_verification_reviewers"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VipMembership(Base):
    __tablename__ = "tp_vip_memberships"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(12))  # active | grace | expired | revoked
    period: Mapped[str] = mapped_column(String(8), default="m")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)


# =============================== Phase 3a: economy & games ====================================

class EcoAccount(Base):
    """A member's balance in ONE tenant. The same person has a separate account in every group."""

    __tablename__ = "tp_eco_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tp_eco_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    wallet: Mapped[int] = mapped_column(BigInteger, default=0)
    bank: Mapped[int] = mapped_column(BigInteger, default=0)
    xp: Mapped[int] = mapped_column(BigInteger, default=0)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_daily: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_weekly: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_interest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EcoItem(Base):
    __tablename__ = "tp_eco_items"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tp_eco_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    name: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(String(200), default="")
    effect: Mapped[str] = mapped_column(String(16), default="none")  # none | padlock | work_boost | xp_potion


class EcoInventory(Base):
    __tablename__ = "tp_eco_inventory"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "item_id", name="uq_tp_eco_inventory"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    item_id: Mapped[int] = mapped_column(ForeignKey("tp_eco_items.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(Integer, default=0)


class EcoLedger(Base):
    """Every change to a wallet, for audits and exploit investigation."""

    __tablename__ = "tp_eco_ledger"
    __table_args__ = (Index("ix_tp_eco_ledger_user", "tenant_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    delta: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GameStat(Base):
    __tablename__ = "tp_game_stats"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "game", name="uq_tp_game_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    game: Mapped[str] = mapped_column(String(16))
    plays: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    net: Mapped[int] = mapped_column(BigInteger, default=0)


class Achievement(Base):
    __tablename__ = "tp_achievements"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "code", name="uq_tp_achievement"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LotteryTicket(Base):
    __tablename__ = "tp_lottery_tickets"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tp_lottery_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(_fk_tenant())
    user_id: Mapped[int] = mapped_column(BigInteger)
    tickets: Mapped[int] = mapped_column(Integer, default=0)
