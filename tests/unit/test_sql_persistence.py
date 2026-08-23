"""Fast in-process tests for SQL repository control flow."""

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import inspect

from md_converter.auth.models import Role, Session, User
from md_converter.config import ConfigurationError
from md_converter.persistence.migrations import (
    run_migration_environment,
    upgrade_database,
)
from md_converter.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)

REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260823_01_auth_tables"
)


@pytest.mark.unit
def test_inprocess_sql_repository_control_flow() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    upgrade_database(engine)
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        "sessions",
        "users",
    }
    assert DatabaseReadinessProbe(engine).is_ready()
    users = SqlUserRepository(engine)
    sessions = SqlSessionRepository(engine)

    admin = users.bootstrap_admin(" Admin ", "admin", "hash:" + "admin")
    assert users.bootstrap_admin("ADMIN", "admin", "unused") == admin
    regular = User(uuid4(), "Alice", "alice", "hash:alice", Role.USER)
    users.create(regular)
    assert users.get_by_id(uuid4()) is None
    assert users.get_by_normalized_username("missing") is None
    assert users.list() == [admin, regular]
    with pytest.raises(KeyError):
        users.create(User(uuid4(), "ALICE", "alice", "other", Role.USER))
    with pytest.raises(ConfigurationError):
        users.bootstrap_admin("Alice", "alice", "unused")

    assert users.commit_verified_login(admin.id, 1, None) is None
    committed = users.commit_verified_login(admin.id, 0, "hash:" + "upgraded")
    assert committed is not None
    assert committed.password_hash.endswith("upgraded")
    changed = users.update_security(admin.id, active=False)
    assert changed is not None
    assert not changed.active
    assert users.commit_verified_login(admin.id, 1, None) is None
    rehashed = users.update_security(admin.id, password_hash="hash:" + "reset")
    assert rehashed is not None
    assert rehashed.password_hash.endswith("reset")
    assert users.update_security(uuid4()) is None

    now = datetime(2026, 8, 23, tzinfo=UTC)
    session = Session(
        token_digest="a" * 64,
        csrf_digest="b" * 64,
        user_id=regular.id,
        auth_version=0,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )
    sessions.create(session)
    loaded = sessions.get(session.token_digest)
    assert loaded == session
    assert loaded is not None
    loaded.last_seen_at += timedelta(seconds=1)
    sessions.save(loaded)
    assert sessions.get(session.token_digest) == loaded
    sessions.save(
        Session(
            token_digest="c" * 64,
            csrf_digest="d" * 64,
            user_id=regular.id,
            auth_version=0,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now,
            absolute_expires_at=now,
        )
    )
    sessions.revoke("missing")
    sessions.revoke_user(regular.id)
    assert sessions.get(session.token_digest) is None
    engine.dispose()


@pytest.mark.unit
def test_database_url_helpers_select_the_expected_drivers() -> None:
    assert standalone_database_url(Path("/data")).endswith("/data/metadata.sqlite3")
    engine = create_database_engine("postgresql://database/application")
    assert engine.url.drivername == "postgresql+psycopg"
    engine.dispose()


@pytest.mark.unit
def test_alembic_environment_and_revision_are_directly_verifiable(
    mocker: MockerFixture,
) -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    context = mocker.MagicMock()
    with engine.connect() as connection:
        context.config.attributes = {"connection": connection}
        run_migration_environment(context)
    context.configure.assert_called_once()
    context.begin_transaction.return_value.__enter__.assert_called_once()
    context.run_migrations.assert_called_once()

    operations = mocker.patch.object(REVISION, "op")
    REVISION.upgrade()
    assert operations.create_table.call_count == 2
    operations.create_index.assert_called_once()
    REVISION.downgrade()
    operations.drop_index.assert_called_once()
    assert operations.drop_table.call_count == 2
    engine.dispose()


@pytest.mark.unit
def test_alembic_environment_rejects_an_unmanaged_connection(
    mocker: MockerFixture,
) -> None:
    context = mocker.MagicMock()
    context.config.attributes = {"connection": object()}
    with pytest.raises(RuntimeError, match="application-managed"):
        run_migration_environment(context)
