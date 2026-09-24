"""Permissioned author records, typed filling templates, and Composer fill plans.

Revision ID: 20260924_25
Revises: 20260923_24
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_25"
down_revision: str | None = "20260923_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), primary_key=True)


def _time(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.add_column(
        "composer_model_steps",
        sa.Column("author_refs", sa.String(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "composer_model_steps", sa.Column("author_preview_digest", sa.String(64))
    )
    op.add_column("composer_revisions", sa.Column("typed_fill_snapshot", sa.String()))
    op.create_table(
        "author_knowledge",
        _id(),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("fields_json", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint("version > 0", name="ck_author_knowledge_version"),
    )
    op.create_index("ix_author_knowledge_owner", "author_knowledge", ["owner_id", "id"])
    op.create_table(
        "author_knowledge_grants",
        sa.Column(
            "author_id",
            sa.String(36),
            sa.ForeignKey("author_knowledge.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _time("created_at"),
    )
    op.create_index(
        "ix_author_knowledge_grants_user", "author_knowledge_grants", ["user_id"]
    )
    op.create_table(
        "author_knowledge_audit",
        _id(),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("target_user_id", sa.String(36)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("administrator_intervention", sa.Boolean(), nullable=False),
        _time("created_at"),
    )
    op.create_index(
        "ix_author_knowledge_audit_author", "author_knowledge_audit", ["author_id"]
    )
    op.create_table(
        "typed_templates",
        _id(),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.String(36)),
        sa.Column("publication_state", sa.String(16), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint("revision > 0", name="ck_typed_templates_revision"),
        sa.CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_typed_templates_publication",
        ),
    )
    op.create_index("ix_typed_templates_owner", "typed_templates", ["owner_id", "id"])
    op.create_table(
        "typed_template_grants",
        sa.Column(
            "template_id",
            sa.String(36),
            sa.ForeignKey("typed_templates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _time("created_at"),
    )
    op.create_index(
        "ix_typed_template_grants_user", "typed_template_grants", ["user_id"]
    )
    op.create_table(
        "typed_template_versions",
        _id(),
        sa.Column(
            "template_id",
            sa.String(36),
            sa.ForeignKey("typed_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("object_owner_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("schema_json", sa.String(), nullable=False),
        sa.Column("schema_sha256", sa.String(64), nullable=False),
        sa.Column("publication_state", sa.String(16), nullable=False),
        sa.Column("publication_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        _time("created_at"),
        sa.UniqueConstraint(
            "template_id", "number", name="uq_typed_template_versions_number"
        ),
        sa.CheckConstraint("number > 0", name="ck_typed_template_versions_number"),
        sa.CheckConstraint("size > 0", name="ck_typed_template_versions_size"),
        sa.CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_typed_template_versions_publication",
        ),
    )
    op.create_index(
        "ix_typed_template_versions_template",
        "typed_template_versions",
        ["template_id"],
    )
    op.create_index(
        "ix_typed_template_versions_recovery",
        "typed_template_versions",
        ["publication_state", "lease_expires_at"],
    )
    op.create_table(
        "typed_template_audit",
        _id(),
        sa.Column("template_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("version_id", sa.String(36)),
        sa.Column("target_user_id", sa.String(36)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("administrator_intervention", sa.Boolean(), nullable=False),
        _time("created_at"),
    )
    op.create_index(
        "ix_typed_template_audit_template", "typed_template_audit", ["template_id"]
    )
    op.create_table(
        "composer_fill_plans",
        _id(),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column(
            "source_revision_id",
            sa.String(36),
            sa.ForeignKey("composer_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "template_version_id",
            sa.String(36),
            sa.ForeignKey("typed_template_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("template_docx_sha256", sa.String(64), nullable=False),
        sa.Column("template_schema_sha256", sa.String(64), nullable=False),
        sa.Column("author_refs", sa.String(), nullable=False),
        sa.Column("values_json", sa.String(), nullable=False),
        sa.Column("provenance_json", sa.String(), nullable=False),
        sa.Column("questions_json", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result_revision_id", sa.String(36)),
        _time("created_at"),
        _time("updated_at"),
        sa.UniqueConstraint(
            "draft_id", "idempotency_key", name="uq_composer_fill_plan_key"
        ),
        sa.CheckConstraint("version > 0", name="ck_composer_fill_plan_version"),
        sa.CheckConstraint(
            "draft_version > 0", name="ck_composer_fill_plan_draft_version"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'published')",
            name="ck_composer_fill_plan_state",
        ),
    )
    op.create_index(
        "ix_composer_fill_plans_history",
        "composer_fill_plans",
        ["draft_id", "created_at", "id"],
    )
    op.create_index("ix_composer_fill_plans_state", "composer_fill_plans", ["state"])
    op.create_table(
        "composer_fill_plan_decisions",
        _id(),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("composer_fill_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        _time("created_at"),
        sa.UniqueConstraint(
            "plan_id", "idempotency_key", name="uq_composer_fill_plan_decision_key"
        ),
        sa.CheckConstraint(
            "resulting_version > 0", name="ck_composer_fill_plan_decision_version"
        ),
    )


def downgrade() -> None:
    for table in (
        "composer_fill_plan_decisions",
        "composer_fill_plans",
        "typed_template_audit",
        "typed_template_versions",
        "typed_template_grants",
        "typed_templates",
        "author_knowledge_audit",
        "author_knowledge_grants",
        "author_knowledge",
    ):
        op.drop_table(table)
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("composer_revisions", recreate="always") as batch:
            batch.drop_column("typed_fill_snapshot")
        with op.batch_alter_table("composer_model_steps", recreate="always") as batch:
            batch.drop_column("author_preview_digest")
            batch.drop_column("author_refs")
    else:
        op.drop_column("composer_revisions", "typed_fill_snapshot")
        op.drop_column("composer_model_steps", "author_preview_digest")
        op.drop_column("composer_model_steps", "author_refs")
