"""phase 3a: economy, items, ledger, game stats, achievements, lottery; plan limits

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NOW = sa.func.now()
TS = sa.DateTime(timezone=True)


def _id():
    return sa.Column("id", sa.Integer(), autoincrement=True, nullable=False)


def _tenant():
    return sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tp_tenants.id", ondelete="CASCADE"), nullable=False)


def _user():
    return sa.Column("user_id", sa.BigInteger(), nullable=False)


def upgrade() -> None:
    op.create_table("tp_eco_accounts", _id(), _tenant(), _user(),
        sa.Column("wallet", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bank", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("xp", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_daily", TS), sa.Column("last_weekly", TS), sa.Column("last_interest", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "user_id", name="uq_tp_eco_account"))

    op.create_table("tp_eco_items", _id(), _tenant(),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("effect", sa.String(16), nullable=False, server_default="none"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "name", name="uq_tp_eco_item"))

    op.create_table("tp_eco_inventory", _id(), _tenant(), _user(),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("tp_eco_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "item_id", name="uq_tp_eco_inventory"))

    op.create_table("tp_eco_ledger", _id(), _tenant(), _user(),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(48), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_eco_ledger_user", "tp_eco_ledger", ["tenant_id", "user_id"])

    op.create_table("tp_game_stats", _id(), _tenant(), _user(),
        sa.Column("game", sa.String(16), nullable=False),
        sa.Column("plays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("net", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "game", name="uq_tp_game_stat"))

    op.create_table("tp_achievements", _id(), _tenant(), _user(),
        sa.Column("code", sa.String(24), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "code", name="uq_tp_achievement"))

    op.create_table("tp_lottery_tickets", _id(), _tenant(), _user(),
        sa.Column("tickets", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "user_id", name="uq_tp_lottery_user"))

    # Plan changes: the shop is a paid feature; free groups get a daily cap on game plays.
    bind = op.get_bind()
    plans = sa.table("tp_plans", sa.column("code", sa.String), sa.column("features", sa.JSON),
                     sa.column("limits", sa.JSON))
    plays = {"free": 20, "starter": 200, "pro": 1000, "premium": 5000, "ultimate": 100000}
    for code, feats, lims in bind.execute(sa.select(plans.c.code, plans.c.features, plans.c.limits)).all():
        feats, lims = list(feats or []), dict(lims or {})
        if code != "free" and "shop" not in feats:
            feats.append("shop")
        lims["game_plays_per_day"] = plays.get(code, 200)
        bind.execute(plans.update().where(plans.c.code == code).values(features=feats, limits=lims))


def downgrade() -> None:
    for t in ("tp_lottery_tickets", "tp_achievements", "tp_game_stats", "tp_eco_ledger",
              "tp_eco_inventory", "tp_eco_items", "tp_eco_accounts"):
        op.drop_table(t)
