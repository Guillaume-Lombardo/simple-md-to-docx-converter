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
    # SQLite 3.34 has no DROP COLUMN. Alembic's batch operation performs a
    # constraint-, index-, and foreign-key-aware table copy on SQLite while
    # retaining native ALTER TABLE operations on PostgreSQL.
    bind = op.get_bind()
    sqlite_triggers: tuple[tuple[str, str], ...] = ()
    recreate = "auto"
    if bind.dialect.name == "sqlite":
        sqlite_triggers = tuple(
            bind.execute(
                sa.text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND sql LIKE :table_name"
                ),
                {"table_name": f"%{TABLE}%"},
            ).tuples()
        )
        for name, _sql in sqlite_triggers:
            quoted_name = name.replace('"', '""')
            bind.execute(sa.text(f'DROP TRIGGER "{quoted_name}"'))
        recreate = "always"

    with op.batch_alter_table(TABLE, recreate=recreate) as batch:
        batch.drop_column("result_manifest_object_id")
        batch.drop_column("source_size")
        batch.drop_column("source_sha256")
        batch.drop_column("source_kind")
        batch.drop_column("source_filename")

    for _name, sql in sqlite_triggers:
        bind.execute(sa.text(sql))
