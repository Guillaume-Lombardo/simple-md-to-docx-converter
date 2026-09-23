"""Preserve the exact Composer source origin family.

Revision ID: 20260923_22
Revises: 20260923_21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_22"
down_revision: str | None = "20260923_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "composer_sources",
        sa.Column("kind", sa.String(24), nullable=False, server_default="upload"),
    )
    op.execute(
        "UPDATE composer_sources SET kind = 'conversion_result' "
        "WHERE origin_job_id IS NOT NULL"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("composer_sources", recreate="always") as batch:
            batch.drop_column("kind")
    else:
        op.drop_column("composer_sources", "kind")
