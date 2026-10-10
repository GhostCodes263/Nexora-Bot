"""phase 2: moderation, management, plans/rentals, payments, verification, VIP

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NOW = sa.func.now()
EMPTY_JSON = sa.text("'{}'")
EMPTY_LIST = sa.text("'[]'")
TS = sa.DateTime(timezone=True)


def _id():
    return sa.Column("id", sa.Integer(), autoincrement=True, nullable=False)


def _tenant(ondelete="CASCADE", nullable=False, unique=False):
    return sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tp_tenants.id", ondelete=ondelete),
                     nullable=nullable, unique=unique)


def _created(name="created_at"):
    return sa.Column(name, TS, nullable=False, server_default=NOW)


def upgrade() -> None:
    op.create_table("tp_warnings", _id(), _tenant(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger()),
        sa.Column("reason", sa.String(256), nullable=False, server_default=""),
        _created(), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_warnings_tenant_user", "tp_warnings", ["tenant_id", "user_id"])

    op.create_table("tp_mod_actions", _id(), _tenant(),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger()),
        sa.Column("reason", sa.String(256), nullable=False, server_default=""),
        sa.Column("until", TS), _created(), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_mod_actions_tenant_target", "tp_mod_actions", ["tenant_id", "target_id"])

    op.create_table("tp_mod_notes", _id(), _tenant(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        _created(), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_mod_notes_tenant_user", "tp_mod_notes", ["tenant_id", "user_id"])

    op.create_table("tp_custom_commands", _id(), _tenant(),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("response", sa.String(2000), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "name", name="uq_tp_custom_cmd"))

    op.create_table("tp_triggers", _id(), _tenant(),
        sa.Column("keyword", sa.String(64), nullable=False),
        sa.Column("response", sa.String(2000), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "keyword", name="uq_tp_trigger"))

    op.create_table("tp_scheduled_messages", _id(), _tenant(),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(3500), nullable=False),
        sa.Column("run_at", TS, nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.BigInteger()),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_sched_due", "tp_scheduled_messages", ["sent", "run_at"])

    op.create_table("tp_member_events", _id(), _tenant(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        _created(), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_member_events_tenant_time", "tp_member_events", ["tenant_id", "created_at"])

    op.create_table("tp_captcha_pending", _id(), _tenant(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("expires_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "user_id", name="uq_tp_captcha_user"))
    op.create_index("ix_tp_captcha_expires", "tp_captcha_pending", ["expires_at"])

    plans = op.create_table("tp_plans",
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("price_month_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_year_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stars_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stars_year", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("features", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("limits", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("code"))

    op.create_table("tp_rentals", _id(), _tenant(unique=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_code", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payment_status", sa.String(12), nullable=False, server_default="none"),
        sa.Column("started_at", TS, nullable=False, server_default=NOW),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("grace_until", TS),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reminders", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        _created(), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_rentals_customer_id", "tp_rentals", ["customer_id"])

    op.create_table("tp_trial_history", _id(), _tenant(unique=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_code", sa.String(16), nullable=False),
        sa.Column("started_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_trial_history_customer_id", "tp_trial_history", ["customer_id"])

    op.create_table("tp_payments", _id(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product", sa.String(8), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tp_tenants.id", ondelete="SET NULL")),
        sa.Column("plan_code", sa.String(16)),
        sa.Column("period", sa.String(2), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("telegram_charge_id", sa.String(128), nullable=False),
        sa.Column("provider_charge_id", sa.String(128)),
        sa.Column("payload", sa.String(128), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="paid"),
        _created(), sa.Column("refunded_at", TS),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_charge_id", name="uq_tp_payment_charge"))
    op.create_index("ix_tp_payments_user", "tp_payments", ["user_id"])
    op.create_index("ix_tp_payments_tenant", "tp_payments", ["tenant_id"])

    op.create_table("tp_verification_applications", _id(),
        sa.Column("public_id", sa.String(12), nullable=False, unique=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("answers", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("photo_file_id", sa.String(256)),
        sa.Column("reviewer_note", sa.String(500)),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
        _created(), _created("updated_at"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_verification_applications_user_id", "tp_verification_applications", ["user_id"])

    op.create_table("tp_verification_actions", _id(),
        sa.Column("application_id", sa.Integer(),
                  sa.ForeignKey("tp_verification_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        _created(), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_verif_actions_app", "tp_verification_actions", ["application_id"])

    op.create_table("tp_verification_reviewers",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("added_by", sa.BigInteger()), _created(), sa.PrimaryKeyConstraint("user_id"))

    op.create_table("tp_vip_memberships",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("period", sa.String(8), nullable=False, server_default="m"),
        sa.Column("started_at", TS, nullable=False, server_default=NOW),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("grace_until", TS),
        sa.Column("reminders", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("note", sa.Text()),
        sa.PrimaryKeyConstraint("user_id"))

    # Seed plans (editable afterwards by the owner with /setplan).
    lim = lambda f, c, t, s: {"filters": f, "custom_commands": c, "triggers": t, "scheduled": s}  # noqa: E731
    base = ["moderation_basic"]
    starter = base + ["welcome", "captcha", "automod_basic", "auto_replies", "custom_commands", "scheduled_messages"]
    pro = starter + ["automod_advanced"]
    op.bulk_insert(plans, [
        dict(code="free", name="Free", price_month_cents=0, price_year_cents=0, stars_month=0, stars_year=0,
             features=base, limits=lim(5, 0, 0, 0), sort=0, is_active=True),
        dict(code="starter", name="Starter", price_month_cents=399, price_year_cents=3999, stars_month=200,
             stars_year=2000, features=starter, limits=lim(25, 5, 10, 5), sort=1, is_active=True),
        dict(code="pro", name="Pro", price_month_cents=899, price_year_cents=8999, stars_month=450,
             stars_year=4500, features=pro, limits=lim(100, 25, 50, 25), sort=2, is_active=True),
        dict(code="premium", name="Premium", price_month_cents=1799, price_year_cents=17999, stars_month=900,
             stars_year=9000, features=pro, limits=lim(300, 100, 200, 100), sort=3, is_active=True),
        dict(code="ultimate", name="Ultimate", price_month_cents=3499, price_year_cents=34999,
             stars_month=1750, stars_year=17500, features=pro, limits=lim(1000, 1000, 1000, 500),
             sort=4, is_active=True),
    ])


def downgrade() -> None:
    for t in ("tp_vip_memberships", "tp_verification_reviewers", "tp_verification_actions",
              "tp_verification_applications", "tp_payments", "tp_trial_history", "tp_rentals", "tp_plans",
              "tp_captcha_pending", "tp_member_events", "tp_scheduled_messages", "tp_triggers",
              "tp_custom_commands", "tp_mod_notes", "tp_mod_actions", "tp_warnings"):
        op.drop_table(t)
