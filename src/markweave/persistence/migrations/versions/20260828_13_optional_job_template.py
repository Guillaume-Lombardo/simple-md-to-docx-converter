"""Allow conversion jobs to use Pandoc's default reference document.

Revision ID: 20260828_13
Revises: 20260825_12
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_13"
down_revision: str | None = "20260825_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "conversion_jobs"


def upgrade() -> None:
    bind = op.get_bind()
    recreate = "auto"
    if bind.dialect.name == "sqlite":
        _drop_sqlite_conversion_triggers()
        recreate = "always"
    with op.batch_alter_table(TABLE, recreate=recreate) as batch:
        batch.alter_column("template_id", existing_type=sa.String(36), nullable=True)
        batch.alter_column(
            "template_version_id", existing_type=sa.String(36), nullable=True
        )
        batch.create_check_constraint(
            "ck_conversion_jobs_template_pair",
            "(template_id IS NULL) = (template_version_id IS NULL)",
        )
    _create_conversion_integrity()


def downgrade() -> None:
    bind = op.get_bind()
    template_free = bind.execute(
        sa.text(
            "SELECT 1 FROM conversion_jobs WHERE template_id IS NULL "
            "OR template_version_id IS NULL LIMIT 1"
        )
    ).first()
    if template_free is not None:
        raise RuntimeError("Cannot downgrade while template-free conversion jobs exist")
    if bind.dialect.name == "sqlite":
        _drop_sqlite_conversion_triggers()
        recreate = "always"
    else:
        recreate = "auto"
    with op.batch_alter_table(TABLE, recreate=recreate) as batch:
        batch.drop_constraint("ck_conversion_jobs_template_pair", type_="check")
        batch.alter_column("template_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column(
            "template_version_id", existing_type=sa.String(36), nullable=False
        )
    _create_legacy_conversion_integrity()


def _create_conversion_integrity() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION enforce_conversion_template_integrity()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'UPDATE'
                   AND ROW(OLD.template_id, OLD.template_version_id)
                       IS NOT DISTINCT FROM
                       ROW(NEW.template_id, NEW.template_version_id) THEN
                    RETURN NEW;
                END IF;
                IF NEW.template_id IS NULL AND NEW.template_version_id IS NULL THEN
                    RETURN NEW;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM templates t JOIN template_versions v
                      ON v.template_id = t.id AND v.id = NEW.template_version_id
                    WHERE t.id = NEW.template_id AND t.status = 'active'
                      AND t.publication_state = 'published'
                      AND t.current_version_id = NEW.template_version_id
                      AND v.publication_state = 'published'
                ) THEN
                    RAISE EXCEPTION 'conversion template pair unavailable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        return
    _create_sqlite_conversion_triggers(allow_default=True)


def _create_legacy_conversion_integrity() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION enforce_conversion_template_integrity()
            RETURNS trigger AS $$
            BEGIN
                IF (TG_OP = 'INSERT' OR ROW(OLD.template_id, OLD.template_version_id)
                    IS DISTINCT FROM ROW(NEW.template_id, NEW.template_version_id))
                   AND NOT EXISTS (
                    SELECT 1 FROM templates t JOIN template_versions v
                      ON v.template_id = t.id AND v.id = NEW.template_version_id
                    WHERE t.id = NEW.template_id AND t.status = 'active'
                      AND t.publication_state = 'published'
                      AND t.current_version_id = NEW.template_version_id
                      AND v.publication_state = 'published'
                ) THEN
                    RAISE EXCEPTION 'conversion template pair unavailable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        return
    _create_sqlite_conversion_triggers(allow_default=False)


def _drop_sqlite_conversion_triggers() -> None:
    for name in (
        "conversion_template_integrity",
        "conversion_template_update_integrity",
        "template_delete_restrict",
        "template_version_delete_restrict",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")


def _create_sqlite_conversion_triggers(*, allow_default: bool) -> None:
    default_guard = "NEW.template_id IS NOT NULL AND " if allow_default else ""
    for name, event in (
        ("conversion_template_integrity", "INSERT"),
        (
            "conversion_template_update_integrity",
            "UPDATE OF template_id, template_version_id",
        ),
    ):
        statement = (
            f"CREATE TRIGGER {name} BEFORE {event} ON conversion_jobs "  # noqa: S608 - fixed migration identifiers
            f"""
            FOR EACH ROW BEGIN
              SELECT CASE WHEN {default_guard}NOT EXISTS (
                SELECT 1 FROM templates t JOIN template_versions v
                  ON v.template_id = t.id AND v.id = NEW.template_version_id
                WHERE t.id = NEW.template_id AND t.status = 'active'
                  AND t.publication_state = 'published'
                  AND t.current_version_id = NEW.template_version_id
                  AND v.publication_state = 'published'
              ) THEN RAISE(ABORT, 'conversion template pair unavailable') END;
            END
            """
        )
        op.execute(statement)
    op.execute(
        """
        CREATE TRIGGER template_delete_restrict BEFORE DELETE ON templates FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM conversion_jobs j WHERE j.template_id = OLD.id)
        BEGIN SELECT RAISE(ABORT, 'template is referenced by a conversion'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER template_version_delete_restrict
        BEFORE DELETE ON template_versions FOR EACH ROW
        WHEN EXISTS (
          SELECT 1 FROM conversion_jobs j WHERE j.template_version_id = OLD.id
        )
        BEGIN SELECT RAISE(ABORT, 'template version is referenced by a conversion'); END
        """
    )
