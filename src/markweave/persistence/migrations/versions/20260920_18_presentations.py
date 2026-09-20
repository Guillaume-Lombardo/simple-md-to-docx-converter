"""Add typed reference templates and immutable presentation job options."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_18"
down_revision: str | None = "20260906_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


@contextmanager
def _preserve_sqlite_triggers() -> Iterator[None]:
    """Keep integrity/audit triggers across SQLite 3.34 table-copy operations."""
    bind = op.get_bind()
    triggers = (
        bind.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL ORDER BY name"
            )
        ).all()
        if bind.dialect.name == "sqlite"
        else []
    )
    for name, _sql in triggers:
        quoted = bind.dialect.identifier_preparer.quote(name)
        op.execute(f"DROP TRIGGER {quoted}")
    yield
    for _name, sql in triggers:
        op.execute(sql)


def upgrade() -> None:
    with _preserve_sqlite_triggers():
        op.add_column(
            "templates",
            sa.Column(
                "kind",
                sa.String(8),
                sa.CheckConstraint(
                    "kind IN ('docx', 'pptx')", name="ck_templates_kind"
                ),
                nullable=False,
                server_default="docx",
            ),
        )
        op.add_column(
            "conversion_jobs",
            sa.Column("presentation_options", sa.String(), nullable=True),
        )
        _constraints(True)
    _immutable_fields(create=True)


def downgrade() -> None:
    bind = op.get_bind()
    if (
        bind.execute(
            sa.text("SELECT 1 FROM templates WHERE kind = 'pptx' LIMIT 1")
        ).first()
        or bind.execute(
            sa.text(
                "SELECT 1 FROM conversion_jobs WHERE output IN ('pptx', 'pptx-bundle') LIMIT 1"
            )
        ).first()
    ):
        raise RuntimeError("Cannot downgrade while PowerPoint templates or jobs exist")
    _immutable_fields(create=False)
    with _preserve_sqlite_triggers():
        _constraints(False)
        with op.batch_alter_table(
            "templates", recreate="always" if bind.dialect.name == "sqlite" else "auto"
        ) as batch:
            batch.drop_constraint("ck_templates_kind", type_="check")
            batch.drop_column("kind")


def _constraints(presentations: bool) -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    outputs = "'docx', 'pdf', 'both'" + (
        ", 'pptx', 'pptx-bundle'" if presentations else ""
    )
    steps = (
        "'queued', 'validating', 'rendering', 'docx', 'pdf', 'publishing', 'complete'"
        + (", 'pptx'" if presentations else "")
    )
    with op.batch_alter_table(
        "conversion_jobs", recreate="always" if sqlite else "auto"
    ) as batch:
        batch.drop_constraint("ck_conversion_jobs_output", type_="check")
        batch.drop_constraint("ck_conversion_jobs_step", type_="check")
        batch.create_check_constraint(
            "ck_conversion_jobs_output", f"output IN ({outputs})"
        )
        batch.create_check_constraint("ck_conversion_jobs_step", f"step IN ({steps})")
        if not presentations:
            batch.drop_column("presentation_options")


def _immutable_fields(*, create: bool) -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    for table, column in (
        ("templates", "kind"),
        ("conversion_jobs", "presentation_options"),
    ):
        name = f"{table}_{column}_immutable"
        if not create:
            op.execute(
                f"DROP TRIGGER IF EXISTS {name}" + ("" if sqlite else f" ON {table}")
            )
            if not sqlite:
                op.execute(f"DROP FUNCTION {name}()")
        elif sqlite:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE UPDATE OF {column} ON {table} "
                f"WHEN OLD.{column} IS NOT NEW.{column} "
                "BEGIN SELECT RAISE(ABORT, 'immutable presentation field'); END"
            )
        else:
            op.execute(
                f"CREATE FUNCTION {name}() RETURNS trigger AS $$ BEGIN "
                f"IF OLD.{column} IS DISTINCT FROM NEW.{column} THEN "
                "RAISE EXCEPTION 'immutable presentation field' USING ERRCODE = '23000'; "
                "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
            op.execute(
                f"CREATE TRIGGER {name} BEFORE UPDATE OF {column} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {name}()"
            )
