"""Enforce immutability of audit and cleanup evidence rows.

Revision ID: 20260824_08
Revises: 20260824_07
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_08"
down_revision: str | None = "20260824_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("template_audit_records", "retention_cleanup_runs")


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable retention record'); END"
            )
        return
    op.execute(
        "CREATE FUNCTION reject_immutable_retention_update() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'immutable retention record' USING ERRCODE = '23000'; END $$"
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_retention_update()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
        return
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update ON {table}")
    op.execute("DROP FUNCTION reject_immutable_retention_update()")
