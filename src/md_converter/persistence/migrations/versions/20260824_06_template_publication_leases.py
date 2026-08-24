"""Fence recovery of stale template publications.

Revision ID: 20260824_06
Revises: 20260824_05
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_06"
down_revision: str | None = "20260824_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("template_versions", sa.Column("publication_token", sa.String(36)))
    op.add_column(
        "template_versions",
        sa.Column("publication_lease_expires_at", sa.DateTime(timezone=True)),
    )
    # Rows left pending by an older process are immediately eligible for recovery.
    op.execute(
        "UPDATE template_versions SET publication_token = "
        "'00000000-0000-0000-0000-000000000000', "
        "publication_lease_expires_at = '1970-01-01 00:00:00+00:00' "
        "WHERE publication_state = 'pending'"
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER template_versions_pending_lease_insert
            BEFORE INSERT ON template_versions FOR EACH ROW
            WHEN NEW.publication_state = 'pending' AND
                 (NEW.publication_token IS NULL OR
                  NEW.publication_lease_expires_at IS NULL)
            BEGIN SELECT RAISE(ABORT, 'pending template publication lacks lease'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER template_versions_pending_lease_update
            BEFORE UPDATE ON template_versions FOR EACH ROW
            WHEN NEW.publication_state = 'pending' AND
                 (NEW.publication_token IS NULL OR
                  NEW.publication_lease_expires_at IS NULL)
            BEGIN SELECT RAISE(ABORT, 'pending template publication lacks lease'); END
            """
        )
    else:
        with op.batch_alter_table("template_versions") as batch:
            batch.create_check_constraint(
                "ck_template_versions_pending_lease",
                "publication_state = 'published' OR "
                "(publication_token IS NOT NULL AND "
                "publication_lease_expires_at IS NOT NULL)",
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS template_versions_pending_lease_update")
        op.execute("DROP TRIGGER IF EXISTS template_versions_pending_lease_insert")
    else:
        with op.batch_alter_table("template_versions") as batch:
            batch.drop_constraint("ck_template_versions_pending_lease", type_="check")
    with op.batch_alter_table("template_versions") as batch:
        batch.drop_column("publication_lease_expires_at")
        batch.drop_column("publication_token")
