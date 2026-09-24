"""Persist typed Composer questions and bounded model-step intent.

Revision ID: 20260923_23
Revises: 20260923_22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_23"
down_revision: str | None = "20260923_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "composer_model_steps",
        sa.Column("intent", sa.String(16), nullable=False, server_default="proposal"),
    )
    op.add_column(
        "composer_model_steps", sa.Column("answered_question_id", sa.String(36))
    )
    op.add_column("composer_model_steps", sa.Column("question_id", sa.String(36)))
    op.create_table(
        "composer_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_step_id",
            sa.String(36),
            sa.ForeignKey("composer_model_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("answer_message_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('pending', 'answered')", name="ck_composer_question_state"
        ),
        sa.UniqueConstraint("model_step_id", name="uq_composer_question_model_step"),
    )
    op.create_index(
        "ix_composer_questions_history",
        "composer_questions",
        ["draft_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_composer_questions_history", table_name="composer_questions")
    op.drop_table("composer_questions")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("composer_model_steps", recreate="always") as batch:
            batch.drop_column("question_id")
            batch.drop_column("answered_question_id")
            batch.drop_column("intent")
    else:
        op.drop_column("composer_model_steps", "question_id")
        op.drop_column("composer_model_steps", "answered_question_id")
        op.drop_column("composer_model_steps", "intent")
