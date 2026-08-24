"""Prevent deletion of immutable retention cleanup evidence.

Revision ID: 20260824_09
Revises: 20260824_08
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_09"
down_revision: str | None = "20260824_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "retention_cleanup_runs"
TRIGGER = "retention_cleanup_runs_immutable_delete"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}")
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE DELETE ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'immutable retention record'); END"
        )
        return
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_retention_update()"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}")
        return
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
