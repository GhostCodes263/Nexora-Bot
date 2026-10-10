"""phase 3b: dating / social

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NOW = sa.func.now()
TS = sa.DateTime(timezone=True)


def _id():
    return sa.Column("id", sa.Integer(), autoincrement=True, nullable=False)


def _tenant():
    return sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tp_tenants.id", ondelete="CASCADE"), nullable=False)


def _big(name):
    return sa.Column(name, sa.BigInteger(), nullable=False)


def upgrade() -> None:
    op.create_table("tp_dating_profiles",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("gender", sa.String(30), nullable=False, server_default=""),
        sa.Column("location", sa.String(60), nullable=False, server_default=""),
        sa.Column("interests", sa.String(200), nullable=False, server_default=""),
        sa.Column("bio", sa.String(500), nullable=False, server_default=""),
        sa.Column("looking_for", sa.String(100), nullable=False, server_default=""),
        sa.Column("rel_status", sa.String(30), nullable=False, server_default=""),
        sa.Column("photo_file_id", sa.String(256)),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("opted_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("share_contact", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("pref_gender", sa.String(16), nullable=False, server_default="any"),
        sa.Column("pref_min_age", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("pref_max_age", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("user_id"))

    op.create_table("tp_dating_pool", _id(), _tenant(), _big("user_id"), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tp_dating_pool"))

    op.create_table("tp_dating_swipes", _id(), _tenant(), _big("from_id"), _big("to_id"),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "from_id", "to_id", name="uq_tp_dating_swipe"))
    op.create_index("ix_tp_dating_swipes_to", "tp_dating_swipes", ["tenant_id", "to_id"])

    op.create_table("tp_dating_matches", _id(), _tenant(), _big("user_a"), _big("user_b"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_a", "user_b", name="uq_tp_dating_match"))

    op.create_table("tp_dating_blocks", _id(), _big("user_id"), _big("blocked_id"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("user_id", "blocked_id", name="uq_tp_dating_block"))

    op.create_table("tp_dating_reports", _id(), _big("reporter_id"), _big("reported_id"),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tp_tenants.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_tp_dating_reports_reported_id", "tp_dating_reports", ["reported_id"])

    op.create_table("tp_couples", _id(), _tenant(), _big("user_a"), _big("user_b"),
        sa.Column("since", TS, nullable=False, server_default=NOW),
        sa.Column("xp", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_activity", TS),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "user_a", "user_b", name="uq_tp_couple"))


def downgrade() -> None:
    for t in ("tp_couples", "tp_dating_reports", "tp_dating_blocks", "tp_dating_matches", "tp_dating_swipes",
              "tp_dating_pool", "tp_dating_profiles"):
        op.drop_table(t)
