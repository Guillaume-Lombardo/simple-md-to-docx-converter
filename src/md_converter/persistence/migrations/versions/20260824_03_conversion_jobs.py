"""Create the durable conversion queue.

Revision ID: 20260824_03
Revises: 20260823_02
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_03"
down_revision: str | None = "20260823_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("source_object_id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("template_version_id", sa.String(length=36), nullable=False),
        sa.Column("output", sa.String(length=16), nullable=False),
        sa.Column("component_versions", sa.String(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("source_ready", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("result_object_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_completed", sa.Boolean(), nullable=False),
        sa.Column("cleanup_owner", sa.String(length=255), nullable=True),
        sa.Column("cleanup_token", sa.String(length=36), nullable=True),
        sa.Column("cleanup_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 0", name="ck_conversion_jobs_attempt"),
        sa.CheckConstraint(
            "output IN ('docx', 'pdf', 'both')", name="ck_conversion_jobs_output"
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_conversion_jobs_progress",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', "
            "'expired')",
            name="ck_conversion_jobs_state",
        ),
        sa.CheckConstraint(
            "step IN ('queued', 'validating', 'rendering', 'docx', 'pdf', "
            "'publishing', 'complete')",
            name="ck_conversion_jobs_step",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "idempotency_digest",
            name="uq_conversion_jobs_owner_idempotency",
        ),
    )
    op.create_index(
        "ix_conversion_jobs_queue",
        "conversion_jobs",
        ["state", "created_at", "id"],
    )
    op.create_index(
        "ix_conversion_jobs_owner_created",
        "conversion_jobs",
        ["owner_id", "created_at", "id"],
    )
    op.create_index(
        "ix_conversion_jobs_lease_expiry",
        "conversion_jobs",
        ["state", "lease_expires_at"],
    )
    op.create_index(
        "ix_conversion_jobs_terminal_expiry",
        "conversion_jobs",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_conversion_jobs_cleanup",
        "conversion_jobs",
        ["state", "cleanup_completed", "cleanup_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversion_jobs_cleanup", table_name="conversion_jobs")
    op.drop_index("ix_conversion_jobs_terminal_expiry", table_name="conversion_jobs")
    op.drop_index("ix_conversion_jobs_lease_expiry", table_name="conversion_jobs")
    op.drop_index("ix_conversion_jobs_owner_created", table_name="conversion_jobs")
    op.drop_index("ix_conversion_jobs_queue", table_name="conversion_jobs")
    op.drop_table("conversion_jobs")
