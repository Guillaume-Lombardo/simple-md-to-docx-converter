"""Persist versioned role-specific idle-session policy and immutable audit.

Revision ID: 20260901_15
Revises: 20260829_14
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_15"
down_revision: str | None = "20260829_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_TABLE = "idle_session_policy"
AUDIT_TABLE = "idle_session_policy_audit_records"
GUARD_TABLE = "audit_cleanup_guards"


def upgrade() -> None:
    op.create_table(
        POLICY_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_idle_minutes", sa.Integer(), nullable=False),
        sa.Column("admin_idle_minutes", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_idle_session_policy_singleton"),
        sa.CheckConstraint(
            "user_idle_minutes BETWEEN 5 AND 300",
            name="ck_idle_session_policy_user_minutes",
        ),
        sa.CheckConstraint(
            "admin_idle_minutes BETWEEN 5 AND 60",
            name="ck_idle_session_policy_admin_minutes",
        ),
        sa.CheckConstraint("revision > 0", name="ck_idle_session_policy_revision"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        AUDIT_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("old_user_idle_minutes", sa.Integer(), nullable=False),
        sa.Column("old_admin_idle_minutes", sa.Integer(), nullable=False),
        sa.Column("new_user_idle_minutes", sa.Integer(), nullable=False),
        sa.Column("new_admin_idle_minutes", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_idle_session_policy_audit_retention",
        AUDIT_TABLE,
        ["created_at", "id"],
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"CREATE TRIGGER {AUDIT_TABLE}_immutable_update BEFORE UPDATE "
            f"ON {AUDIT_TABLE} BEGIN SELECT RAISE(ABORT, "
            "'immutable retention record'); END"
        )
        op.execute(
            f"CREATE TRIGGER {AUDIT_TABLE}_immutable_delete BEFORE DELETE "  # noqa: S608 - fixed migration identifiers
            f"ON {AUDIT_TABLE} WHEN NOT EXISTS (SELECT 1 FROM {GUARD_TABLE}) "
            "BEGIN SELECT RAISE(ABORT, 'immutable retention record'); END"
        )
        return
    op.execute(
        f"CREATE TRIGGER {AUDIT_TABLE}_immutable_update BEFORE UPDATE ON {AUDIT_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_retention_update()"
    )
    op.execute(
        f"CREATE TRIGGER {AUDIT_TABLE}_immutable_delete BEFORE DELETE ON {AUDIT_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION reject_unauthorized_audit_delete()"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_immutable_update")
        op.execute(f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_immutable_delete")
    else:
        op.execute(
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_immutable_update ON {AUDIT_TABLE}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {AUDIT_TABLE}_immutable_delete ON {AUDIT_TABLE}"
        )
    op.drop_index("ix_idle_session_policy_audit_retention", table_name=AUDIT_TABLE)
    op.drop_table(AUDIT_TABLE)
    op.drop_table(POLICY_TABLE)
