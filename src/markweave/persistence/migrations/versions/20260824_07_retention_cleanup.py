"""Add fenced template retention and cleanup evidence.

Revision ID: 20260824_07
Revises: 20260824_06
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_07"
down_revision: str | None = "20260824_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("template_versions", sa.Column("retention_token", sa.String(36)))
    op.add_column(
        "template_versions",
        sa.Column("retention_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_template_version_retention",
        "template_versions",
        ["created_at", "retention_lease_expires_at"],
    )
    op.create_index(
        "ix_template_audit_retention",
        "template_audit_records",
        ["created_at", "id"],
    )
    op.create_table(
        "retention_cleanup_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_count", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("retention_cleanup_runs")
    op.drop_index("ix_template_audit_retention", table_name="template_audit_records")
    op.drop_index("ix_template_version_retention", table_name="template_versions")
    with op.batch_alter_table("template_versions") as batch:
        batch.drop_column("retention_lease_expires_at")
        batch.drop_column("retention_token")
