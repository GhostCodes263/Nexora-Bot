"""core: users, tenants, roles, audit log, global settings

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.func.now()
EMPTY_JSON = sa.text("'{}'")


def upgrade() -> None:
    op.create_table(
        "tp_users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("is_globally_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tp_global_roles",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("role", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "tp_tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False, server_default=""),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("prefix", sa.String(4), nullable=False, server_default="/"),
        sa.Column("module_overrides", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("settings", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tp_tenants_chat_id", "tp_tenants", ["chat_id"], unique=True)
    op.create_table(
        "tp_tenant_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["tenant_id"], ["tp_tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tp_tenant_role_user"),
    )
    op.create_index("ix_tp_tenant_roles_tenant", "tp_tenant_roles", ["tenant_id"])
    op.create_table(
        "tp_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["tenant_id"], ["tp_tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tp_audit_tenant_created", "tp_audit_logs", ["tenant_id", "created_at"])
    op.create_table(
        "tp_global_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("tp_global_settings")
    op.drop_index("ix_tp_audit_tenant_created", table_name="tp_audit_logs")
    op.drop_table("tp_audit_logs")
    op.drop_index("ix_tp_tenant_roles_tenant", table_name="tp_tenant_roles")
    op.drop_table("tp_tenant_roles")
    op.drop_index("ix_tp_tenants_chat_id", table_name="tp_tenants")
    op.drop_table("tp_tenants")
    op.drop_table("tp_global_roles")
    op.drop_table("tp_users")
