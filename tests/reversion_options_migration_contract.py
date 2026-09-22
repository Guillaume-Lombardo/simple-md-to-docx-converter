"""Populated reverse-option migration round trips on both database profiles."""

from uuid import uuid4

from sqlalchemy import URL, inspect, text

from markweave.auth.models import Role, User
from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
)
from markweave.reversions.options import ReversionOptions
from tests.reversion_job_repository_contracts import submission
from tests.sqlite_compatibility import enforce_sqlite_334_alter_grammar


def exercise_reversion_options_migration(url: str | URL) -> None:
    """Preserve populated legacy jobs through option removal and restoration."""

    engine = create_database_engine(url)
    try:
        upgrade_database(engine)
        owner = User(uuid4(), "Owner", f"migration-{uuid4()}", "hash:user", Role.USER)
        SqlUserRepository(engine).create(owner)
        repository = SqlReversionJobRepository(engine)
        created, _ = repository.create(submission(owner.id))
        before = inspect(engine)
        foreign_keys = before.get_foreign_keys("reversion_jobs")
        indexes = before.get_indexes("reversion_jobs")
        checks = before.get_check_constraints("reversion_jobs")
        if engine.dialect.name == "sqlite":
            enforce_sqlite_334_alter_grammar(engine)
        try:
            downgrade_database(engine, "20260920_18")
            downgraded = inspect(engine)
            assert "options" not in {
                column["name"] for column in downgraded.get_columns("reversion_jobs")
            }
            assert downgraded.get_foreign_keys("reversion_jobs") == foreign_keys
            assert downgraded.get_indexes("reversion_jobs") == indexes
            assert downgraded.get_check_constraints("reversion_jobs") == checks
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT source_sha256 FROM reversion_jobs WHERE id = :id"),
                        {"id": str(created.id)},
                    ).scalar_one()
                    == created.source_sha256
                )
        finally:
            upgrade_database(engine)
        restored = repository.get_internal(created.id)
        assert restored == created
        assert restored.options == ReversionOptions()
    finally:
        engine.dispose()
