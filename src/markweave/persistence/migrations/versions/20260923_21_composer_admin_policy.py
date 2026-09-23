"""Persist administrator-approved Composer egress policy.

Revision ID: 20260923_21
Revises: 20260923_20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_21"
down_revision: str | None = "20260923_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "composer_key_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_composer_key_identity_singleton"),
    )
    op.create_table(
        "composer_admin_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("destinations", sa.String(), nullable=False),
        sa.Column("networks", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_composer_admin_policy_singleton"),
        sa.CheckConstraint("version > 0", name="ck_composer_admin_policy_version"),
    )
    op.create_table(
        "composer_admin_policy_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("old_enabled", sa.Boolean(), nullable=False),
        sa.Column("new_enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_composer_admin_policy_audit_retention",
        "composer_admin_policy_audit",
        ["created_at", "id"],
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER composer_admin_policy_audit_immutable_update "
            "BEFORE UPDATE ON composer_admin_policy_audit BEGIN "
            "SELECT RAISE(ABORT, 'Composer policy audit is immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER composer_admin_policy_audit_immutable_delete "
            "BEFORE DELETE ON composer_admin_policy_audit "
            "WHEN NOT EXISTS (SELECT 1 FROM audit_cleanup_guards) "
            "BEGIN SELECT RAISE(ABORT, 'immutable Composer audit'); END"
        )
    elif op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER composer_admin_policy_audit_immutable_update "
            "BEFORE UPDATE ON composer_admin_policy_audit FOR EACH ROW "
            "EXECUTE FUNCTION reject_composer_record_mutation()"
        )
        op.execute(
            "CREATE TRIGGER composer_admin_policy_audit_immutable_delete "
            "BEFORE DELETE ON composer_admin_policy_audit FOR EACH ROW "
            "EXECUTE FUNCTION reject_unauthorized_audit_delete()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS composer_admin_policy_audit_immutable_delete"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS composer_admin_policy_audit_immutable_update"
        )
    elif op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS composer_admin_policy_audit_immutable_delete "
            "ON composer_admin_policy_audit"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS composer_admin_policy_audit_immutable_update "
            "ON composer_admin_policy_audit"
        )
    op.drop_index(
        "ix_composer_admin_policy_audit_retention",
        table_name="composer_admin_policy_audit",
    )
    op.drop_table("composer_admin_policy_audit")
    op.drop_table("composer_admin_policy")
    op.drop_table("composer_key_identity")
