"""Create template identity, preference, and fallback tables.

Revision ID: 20260823_02
Revises: 20260823_01
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_02"
down_revision: str | None = "20260823_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("normalized_description", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "normalized_name <> ''", name="ck_templates_normalized_name_nonempty"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_templates_status"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_templates_owner_id", "templates", ["owner_id"])
    op.create_index("ix_templates_status", "templates", ["status"])
    op.create_index("ix_templates_search_order", "templates", ["normalized_name", "id"])
    op.create_table(
        "template_preferences",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_template_preferences_template_id",
        "template_preferences",
        ["template_id"],
    )
    op.create_table(
        "system_template_selection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fallback_template_id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_system_template_selection_singleton"),
        sa.ForeignKeyConstraint(
            ["fallback_template_id"], ["templates.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_owner_immutability_trigger()


def downgrade() -> None:
    _drop_owner_immutability_trigger()
    op.drop_table("system_template_selection")
    op.drop_index(
        "ix_template_preferences_template_id", table_name="template_preferences"
    )
    op.drop_table("template_preferences")
    op.drop_index("ix_templates_search_order", table_name="templates")
    op.drop_index("ix_templates_status", table_name="templates")
    op.drop_index("ix_templates_owner_id", table_name="templates")
    op.drop_table("templates")


def _create_owner_immutability_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_template_owner_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'template owner is immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER templates_owner_immutable
            BEFORE UPDATE OF owner_id ON templates
            FOR EACH ROW
            WHEN (OLD.owner_id IS DISTINCT FROM NEW.owner_id)
            EXECUTE FUNCTION reject_template_owner_change()
            """
        )
        return
    op.execute(
        """
        CREATE TRIGGER templates_owner_immutable
        BEFORE UPDATE OF owner_id ON templates
        FOR EACH ROW WHEN OLD.owner_id <> NEW.owner_id
        BEGIN
            SELECT RAISE(ABORT, 'template owner is immutable');
        END
        """
    )


def _drop_owner_immutability_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS templates_owner_immutable ON templates")
        op.execute("DROP FUNCTION IF EXISTS reject_template_owner_change()")
        return
    op.execute("DROP TRIGGER IF EXISTS templates_owner_immutable")
