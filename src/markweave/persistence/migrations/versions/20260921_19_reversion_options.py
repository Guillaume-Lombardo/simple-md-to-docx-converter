"""Persist reverse extraction options.

Revision ID: 20260921_19
Revises: 20260920_18
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_19"
down_revision: str | None = "20260920_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_OPTIONS = '{"extraction":"anydoc","include_images":true,"include_notes":true}'


def upgrade() -> None:
    """Add canonical reverse options while preserving legacy job behavior."""

    op.add_column(
        "reversion_jobs",
        sa.Column(
            "options",
            sa.String(),
            nullable=False,
            server_default=_DEFAULT_OPTIONS,
        ),
    )


def downgrade() -> None:
    """Remove persisted reverse options."""

    op.drop_column("reversion_jobs", "options")
