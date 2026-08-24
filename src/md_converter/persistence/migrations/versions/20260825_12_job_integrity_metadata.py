"""Add frozen source integrity and result manifest metadata.

Revision ID: 20260825_12
Revises: 20260824_11
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_12"
down_revision: str | None = "20260824_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "conversion_jobs"


def upgrade() -> None:
    # Historical rows cannot be safely backfilled from SQL because source bytes live
    # in the private object store. Nullable legacy metadata therefore fails closed
    # if an old non-terminal job is ever claimed, while terminal history stays readable.
    op.add_column(TABLE, sa.Column("source_filename", sa.String(255)))
    op.add_column(TABLE, sa.Column("source_kind", sa.String(16)))
    op.add_column(TABLE, sa.Column("source_sha256", sa.String(64)))
    op.add_column(TABLE, sa.Column("source_size", sa.Integer()))
    op.add_column(TABLE, sa.Column("result_manifest_object_id", sa.String(36)))


def downgrade() -> None:
    op.drop_column(TABLE, "result_manifest_object_id")
    op.drop_column(TABLE, "source_size")
    op.drop_column(TABLE, "source_sha256")
    op.drop_column(TABLE, "source_kind")
    op.drop_column(TABLE, "source_filename")
