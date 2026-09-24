"""Link approved Composer Markdown revisions to durable conversion jobs.

Revision ID: 20260923_24
Revises: 20260923_23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_24"
down_revision: str | None = "20260923_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "composer_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column(
            "source_revision_id",
            sa.String(36),
            sa.ForeignKey("composer_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_draft_version", sa.Integer(), nullable=False),
        sa.Column("approved_markdown_sha256", sa.String(64), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("output", sa.String(16), nullable=False),
        sa.Column("template_id", sa.String(36)),
        sa.Column("template_version_id", sa.String(36)),
        sa.Column("presentation_options", sa.String()),
        sa.Column("component_versions", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("result_revision_id", sa.String(36)),
        sa.Column("publication_key", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_id", "idempotency_key", name="uq_composer_generation_idempotency"
        ),
        sa.UniqueConstraint("job_id", name="uq_composer_generation_job"),
        sa.CheckConstraint(
            "output IN ('docx', 'pdf', 'pptx')", name="ck_composer_generation_output"
        ),
        sa.CheckConstraint(
            "expected_draft_version > 0", name="ck_composer_generation_version"
        ),
    )
    op.create_index(
        "ix_composer_generations_history",
        "composer_generations",
        ["draft_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_composer_generations_history", table_name="composer_generations")
    op.drop_table("composer_generations")
