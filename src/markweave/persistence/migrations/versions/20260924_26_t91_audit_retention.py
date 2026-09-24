"""Protect and index T91 audit evidence for bounded retention.

Revision ID: 20260924_26
Revises: 20260924_25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260924_26"
down_revision: str | None = "20260924_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_TABLES = ("author_knowledge_audit", "typed_template_audit")


def upgrade() -> None:
    for table in _AUDIT_TABLES:
        op.create_index(f"ix_{table}_retention", table, ["created_at", "id"])
        if op.get_bind().dialect.name == "sqlite":
            op.execute(
                f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable Composer audit'); END"
            )
            op.execute(
                f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "  # noqa: S608 - fixed migration identifiers
                "WHEN NOT EXISTS (SELECT 1 FROM audit_cleanup_guards) "
                "BEGIN SELECT RAISE(ABORT, 'immutable Composer audit'); END"
            )
        else:
            op.execute(
                f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_composer_record_mutation()"
            )
            op.execute(
                f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_unauthorized_audit_delete()"
            )


def downgrade() -> None:
    for table in _AUDIT_TABLES:
        if op.get_bind().dialect.name == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update ON {table}")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete ON {table}")
        op.drop_index(f"ix_{table}_retention", table_name=table)
