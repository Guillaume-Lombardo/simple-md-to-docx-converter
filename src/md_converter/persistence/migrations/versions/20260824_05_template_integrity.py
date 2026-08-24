"""Add crash-safe template publication and relational integrity.

Revision ID: 20260824_05
Revises: 20260824_04
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_05"
down_revision: str | None = "20260824_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _drop_legacy_immutability_trigger()
    op.add_column(
        "templates",
        sa.Column(
            "publication_state",
            sa.String(16),
            nullable=False,
            server_default="published",
        ),
    )
    for name, default in (
        ("declared_fonts", "[]"),
        ("resolved_fonts", "[]"),
        ("validation_trace", "[]"),
    ):
        op.add_column(
            "template_versions",
            sa.Column(name, sa.String(), nullable=False, server_default=default),
        )
    op.add_column(
        "template_versions",
        sa.Column(
            "publication_state",
            sa.String(16),
            nullable=False,
            server_default="published",
        ),
    )
    with op.batch_alter_table("templates") as batch:
        batch.create_check_constraint(
            "ck_templates_publication_state",
            "publication_state IN ('pending', 'published', 'deleting')",
        )
    with op.batch_alter_table("template_versions") as batch:
        batch.create_unique_constraint(
            "uq_template_versions_pair", ["template_id", "id"]
        )
        batch.create_check_constraint(
            "ck_template_versions_publication_state",
            "publication_state IN ('pending', 'published')",
        )
    if op.get_bind().dialect.name == "sqlite":
        _create_sqlite_owner_immutability_trigger()
    _create_integrity_triggers()


def downgrade() -> None:
    _drop_integrity_triggers()
    with op.batch_alter_table("template_versions") as batch:
        batch.drop_constraint("ck_template_versions_publication_state", type_="check")
        batch.drop_constraint("uq_template_versions_pair", type_="unique")
        batch.drop_column("publication_state")
        batch.drop_column("validation_trace")
        batch.drop_column("resolved_fonts")
        batch.drop_column("declared_fonts")
    with op.batch_alter_table("templates") as batch:
        batch.drop_constraint("ck_templates_publication_state", type_="check")
        batch.drop_column("publication_state")
    if op.get_bind().dialect.name == "sqlite":
        _create_sqlite_owner_immutability_trigger()
    _create_legacy_immutability_trigger()


def _drop_legacy_immutability_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS template_versions_immutable ON template_versions"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_template_version_change()")
        return
    op.execute("DROP TRIGGER IF EXISTS template_versions_immutable")


def _create_legacy_immutability_trigger() -> None:
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
        FOR EACH ROW BEGIN
          SELECT RAISE(ABORT, 'template versions are immutable');
        END
        """
    )


def _create_sqlite_owner_immutability_trigger() -> None:
    """Restore the T14 trigger removed by SQLite batch table recreation."""
    op.execute(
        """
        CREATE TRIGGER templates_owner_immutable
        BEFORE UPDATE OF owner_id ON templates
        FOR EACH ROW WHEN OLD.owner_id <> NEW.owner_id
        BEGIN SELECT RAISE(ABORT, 'template owner is immutable'); END
        """
    )


def _create_integrity_triggers() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _create_postgresql_integrity_triggers()
        return
    _create_sqlite_integrity_triggers()


def _create_postgresql_integrity_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_template_version_integrity()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND
               ROW(OLD.id, OLD.template_id, OLD.version_number, OLD.object_owner_id,
                   OLD.sha256, OLD.size, OLD.created_at, OLD.created_by,
                   OLD.restored_from_version_id, OLD.declared_fonts,
                   OLD.resolved_fonts, OLD.validation_trace) IS DISTINCT FROM
               ROW(NEW.id, NEW.template_id, NEW.version_number, NEW.object_owner_id,
                   NEW.sha256, NEW.size, NEW.created_at, NEW.created_by,
                   NEW.restored_from_version_id, NEW.declared_fonts,
                   NEW.resolved_fonts, NEW.validation_trace) THEN
                RAISE EXCEPTION 'template versions are immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.publication_state <> NEW.publication_state
               AND NOT (OLD.publication_state = 'pending'
                        AND NEW.publication_state = 'published') THEN
                RAISE EXCEPTION 'invalid template version publication transition';
            END IF;
            IF TG_OP = 'INSERT' AND NOT EXISTS (
                SELECT 1 FROM templates t WHERE t.id = NEW.template_id
                AND t.owner_id = NEW.object_owner_id
            ) THEN RAISE EXCEPTION 'template version owner mismatch'; END IF;
            IF TG_OP = 'INSERT'
               AND NEW.restored_from_version_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM template_versions v
                   WHERE v.id = NEW.restored_from_version_id
                   AND v.template_id = NEW.template_id) THEN
                RAISE EXCEPTION 'template restore source mismatch';
            END IF;
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM conversion_jobs j
                WHERE j.template_version_id = OLD.id
            ) THEN RAISE EXCEPTION 'template is referenced by a conversion'; END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_template_current_integrity()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.publication_state <> NEW.publication_state
               AND NOT (
                 (OLD.publication_state = 'pending'
                  AND NEW.publication_state = 'published') OR
                 (OLD.publication_state = 'published'
                  AND NEW.publication_state = 'deleting')
               ) THEN RAISE EXCEPTION 'invalid template publication transition'; END IF;
            IF TG_OP <> 'DELETE' AND NEW.current_version_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM template_versions v
                   WHERE v.id = NEW.current_version_id AND v.template_id = NEW.id
                   AND v.publication_state = 'published') THEN
                RAISE EXCEPTION 'template current version mismatch';
            END IF;
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM conversion_jobs j WHERE j.template_id = OLD.id
            ) THEN RAISE EXCEPTION 'template is referenced by a conversion'; END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
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
            ) THEN RAISE EXCEPTION 'conversion template pair unavailable'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for name, table, events, function in (
        (
            "template_versions_immutable",
            "template_versions",
            "UPDATE",
            "enforce_template_version_integrity",
        ),
        (
            "template_versions_integrity",
            "template_versions",
            "INSERT OR DELETE",
            "enforce_template_version_integrity",
        ),
        (
            "templates_current_integrity",
            "templates",
            "INSERT OR UPDATE OR DELETE",
            "enforce_template_current_integrity",
        ),
        (
            "conversion_template_integrity",
            "conversion_jobs",
            "INSERT OR UPDATE",
            "enforce_conversion_template_integrity",
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {events} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )


def _create_sqlite_integrity_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER template_versions_immutable
        BEFORE UPDATE ON template_versions FOR EACH ROW
        WHEN OLD.id <> NEW.id OR OLD.template_id <> NEW.template_id
          OR OLD.version_number <> NEW.version_number
          OR OLD.object_owner_id <> NEW.object_owner_id OR OLD.sha256 <> NEW.sha256
          OR OLD.size <> NEW.size OR OLD.created_at <> NEW.created_at
          OR OLD.created_by <> NEW.created_by
          OR OLD.restored_from_version_id IS NOT NEW.restored_from_version_id
          OR OLD.declared_fonts <> NEW.declared_fonts
          OR OLD.resolved_fonts <> NEW.resolved_fonts
          OR OLD.validation_trace <> NEW.validation_trace
          OR (OLD.publication_state <> NEW.publication_state
              AND NOT (OLD.publication_state = 'pending'
                       AND NEW.publication_state = 'published'))
        BEGIN SELECT RAISE(ABORT, 'template versions are immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER template_versions_integrity BEFORE INSERT ON template_versions
        FOR EACH ROW BEGIN
          SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM templates t
            WHERE t.id = NEW.template_id AND t.owner_id = NEW.object_owner_id)
            THEN RAISE(ABORT, 'template version owner mismatch') END;
          SELECT CASE WHEN NEW.restored_from_version_id IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM template_versions v WHERE v.id = NEW.restored_from_version_id
             AND v.template_id = NEW.template_id)
            THEN RAISE(ABORT, 'template restore source mismatch') END;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER templates_publication_transition
        BEFORE UPDATE OF publication_state ON templates FOR EACH ROW
        WHEN OLD.publication_state <> NEW.publication_state AND NOT (
          (OLD.publication_state = 'pending' AND NEW.publication_state = 'published')
          OR (OLD.publication_state = 'published' AND NEW.publication_state = 'deleting')
        )
        BEGIN SELECT RAISE(ABORT, 'invalid template publication transition'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER templates_insert_integrity BEFORE INSERT ON templates
        FOR EACH ROW WHEN NEW.current_version_id IS NOT NULL BEGIN
          SELECT RAISE(ABORT, 'template current version mismatch');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER templates_current_integrity
        BEFORE UPDATE OF current_version_id ON templates FOR EACH ROW
        WHEN NEW.current_version_id IS NOT NULL BEGIN
          SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM template_versions v
            WHERE v.id = NEW.current_version_id AND v.template_id = NEW.id
            AND v.publication_state = 'published')
            THEN RAISE(ABORT, 'template current version mismatch') END;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER conversion_template_integrity BEFORE INSERT ON conversion_jobs
        FOR EACH ROW BEGIN
          SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM templates t
            JOIN template_versions v
              ON v.template_id = t.id AND v.id = NEW.template_version_id
            WHERE t.id = NEW.template_id AND t.status = 'active'
            AND t.publication_state = 'published'
            AND t.current_version_id = NEW.template_version_id
            AND v.publication_state = 'published')
            THEN RAISE(ABORT, 'conversion template pair unavailable') END;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER conversion_template_update_integrity
        BEFORE UPDATE OF template_id, template_version_id ON conversion_jobs
        FOR EACH ROW BEGIN
          SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM templates t
            JOIN template_versions v
              ON v.template_id = t.id AND v.id = NEW.template_version_id
            WHERE t.id = NEW.template_id AND t.status = 'active'
            AND t.publication_state = 'published'
            AND t.current_version_id = NEW.template_version_id
            AND v.publication_state = 'published')
            THEN RAISE(ABORT, 'conversion template pair unavailable') END;
        END
        """
    )
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


def _drop_integrity_triggers() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name, table in (
            ("template_versions_immutable", "template_versions"),
            ("template_versions_integrity", "template_versions"),
            ("templates_current_integrity", "templates"),
            ("conversion_template_integrity", "conversion_jobs"),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        for function in (
            "enforce_template_version_integrity",
            "enforce_template_current_integrity",
            "enforce_conversion_template_integrity",
        ):
            op.execute(f"DROP FUNCTION IF EXISTS {function}()")
        return
    for name in (
        "template_versions_immutable",
        "template_versions_integrity",
        "templates_publication_transition",
        "templates_insert_integrity",
        "templates_current_integrity",
        "conversion_template_integrity",
        "conversion_template_update_integrity",
        "template_delete_restrict",
        "template_version_delete_restrict",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
