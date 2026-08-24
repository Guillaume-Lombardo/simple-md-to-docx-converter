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
GUARD_TABLE = "audit_cleanup_guards"
DELETE_FUNCTION = "reject_unauthorized_audit_delete"
DELETE_TABLES = ("template_audit_records", TABLE)


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
    op.create_table(
        GUARD_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'immutable retention record'); END"
        )
        for table in DELETE_TABLES:
            delete_trigger = (
                f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "  # noqa: S608 - fixed migration identifiers
                f"WHEN NOT EXISTS (SELECT 1 FROM {GUARD_TABLE}) "
                "BEGIN SELECT RAISE(ABORT, 'immutable retention record'); END"
            )
            op.execute(delete_trigger)
        return
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_retention_update()"
    )
    delete_function = (
        f"CREATE FUNCTION {DELETE_FUNCTION}() RETURNS trigger "  # noqa: S608 - fixed migration identifiers
        "LANGUAGE plpgsql AS $$ BEGIN IF NOT EXISTS "
        f"(SELECT 1 FROM {GUARD_TABLE}) THEN RAISE EXCEPTION "
        "'immutable retention record' USING ERRCODE = '23000'; "
        "END IF; RETURN OLD; END $$"
    )
    op.execute(delete_function)
    for table in DELETE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {DELETE_FUNCTION}()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}")
        for table in DELETE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
        for table in DELETE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete ON {table}")
        op.execute(f"DROP FUNCTION {DELETE_FUNCTION}()")
    op.drop_index("ix_authentication_audit_retention", table_name=TABLE)
    op.drop_index("ix_authentication_audit_target", table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_table(GUARD_TABLE)
