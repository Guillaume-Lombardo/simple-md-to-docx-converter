"""Create immutable template versions and audit records.

Revision ID: 20260824_04
Revises: 20260824_03
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_04"
down_revision: str | None = "20260824_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "templates", sa.Column("current_version_id", sa.String(36), nullable=True)
    )
    op.create_table(
        "template_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("template_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("object_owner_id", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("restored_from_version_id", sa.String(36), nullable=True),
        sa.CheckConstraint("version_number > 0", name="ck_template_versions_number"),
        sa.CheckConstraint("size > 0", name="ck_template_versions_size"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "template_id", "version_number", name="uq_template_versions_number"
        ),
    )
    op.create_index(
        "ix_template_versions_template_number",
        "template_versions",
        ["template_id", "version_number"],
    )
    op.create_table(
        "template_audit_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("template_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=True),
        sa.Column("administrator_intervention", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_template_audit_target",
        "template_audit_records",
        ["template_id", "created_at"],
    )
    _create_version_immutability_trigger()


def downgrade() -> None:
    _drop_version_immutability_trigger()
    op.drop_index("ix_template_audit_target", table_name="template_audit_records")
    op.drop_table("template_audit_records")
    op.drop_index(
        "ix_template_versions_template_number", table_name="template_versions"
    )
    op.drop_table("template_versions")
    op.drop_column("templates", "current_version_id")
    op.drop_column("templates", "revision")


def _create_version_immutability_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_template_version_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'template versions are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER template_versions_immutable
            BEFORE UPDATE ON template_versions
            FOR EACH ROW EXECUTE FUNCTION reject_template_version_change()
            """
        )
        return
    op.execute(
        """
        CREATE TRIGGER template_versions_immutable
        BEFORE UPDATE ON template_versions
        FOR EACH ROW
        BEGIN
            SELECT RAISE(ABORT, 'template versions are immutable');
        END
        """
    )


def _drop_version_immutability_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS template_versions_immutable ON template_versions"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_template_version_change()")
        return
    op.execute("DROP TRIGGER IF EXISTS template_versions_immutable")
