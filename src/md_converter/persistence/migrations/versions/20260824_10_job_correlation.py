"""Persist safe API-to-worker correlation identifiers.

Revision ID: 20260824_10
Revises: 20260824_09
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_10"
down_revision: str | None = "20260824_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversion_jobs",
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
    )
    op.execute(
        "UPDATE conversion_jobs SET correlation_id = id WHERE correlation_id IS NULL"
    )


def downgrade() -> None:
    op.drop_column("conversion_jobs", "correlation_id")
