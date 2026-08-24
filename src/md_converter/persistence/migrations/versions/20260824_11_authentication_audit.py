"""Add immutable authentication mutation audit records.

Revision ID: 20260824_11
Revises: 20260824_10
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_11"
down_revision: str | None = "20260824_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "authentication_audit_records"
TRIGGER = f"{TABLE}_immutable_update"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("administrator_intervention", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authentication_audit_target",
        TABLE,
        ["target_id", "created_at"],
    )
    op.create_index(
        "ix_authentication_audit_retention",
        TABLE,
        ["created_at", "id"],
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'immutable retention record'); END"
        )
        return
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_retention_update()"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
    op.drop_index("ix_authentication_audit_retention", table_name=TABLE)
    op.drop_index("ix_authentication_audit_target", table_name=TABLE)
    op.drop_table(TABLE)
