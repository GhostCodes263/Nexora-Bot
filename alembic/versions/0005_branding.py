"""branding: custom banners are a Pro-and-up feature

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    plans = sa.table("tp_plans", sa.column("code", sa.String), sa.column("features", sa.JSON))
    for code, feats in bind.execute(sa.select(plans.c.code, plans.c.features)).all():
        feats = list(feats or [])
        if code in ("pro", "premium", "ultimate") and "branding" not in feats:
            feats.append("branding")
            bind.execute(plans.update().where(plans.c.code == code).values(features=feats))


def downgrade() -> None:
    pass
