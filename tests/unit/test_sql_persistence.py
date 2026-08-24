"""Fast in-process tests for SQL repository control flow."""

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from md_converter.auth.models import Role, Session, User
from md_converter.config import ConfigurationError
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import (
    downgrade_database,
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
JOB_REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260824_03_conversion_jobs"
)
RETENTION_REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260824_07_retention_cleanup"
)
IMMUTABILITY_REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260824_08_immutable_retention_records"
)
CLEANUP_EVIDENCE_REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260824_09_immutable_cleanup_evidence"
)


@pytest.mark.unit
def test_inprocess_sql_repository_control_flow() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    upgrade_database(engine)
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        "conversion_jobs",
        "retention_cleanup_runs",
        "sessions",
        "system_template_selection",
        "template_audit_records",
        "template_preferences",
        "template_versions",
        "templates",
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
    data_directory = Path("/data/space ?#%/données")
    standalone_url = standalone_database_url(data_directory)
    assert standalone_url.database == str(data_directory / "metadata.sqlite3")
    sqlite_engine = create_database_engine(standalone_url)
    assert sqlite_engine.url.database == str(data_directory / "metadata.sqlite3")
    assert sqlite_engine.hide_parameters
    sqlite_engine.dispose()
    engine = create_database_engine("postgresql://database/application")
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.hide_parameters
    engine.dispose()


@pytest.mark.unit
def test_sql_failures_have_one_stable_sanitized_boundary(
    mocker: MockerFixture,
) -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA query_only=ON"))
    private_hash = "private-hash-material"
    with pytest.raises(PersistenceError) as caught:
        SqlUserRepository(engine).create(
            User(uuid4(), "Private", "private", private_hash, Role.USER)
        )
    assert str(caught.value) == "Persistence operation failed"
    assert caught.value.__suppress_context__
    assert private_hash not in repr(caught.value)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA query_only=OFF"))

    command = mocker.patch(
        "md_converter.persistence.migrations.command.upgrade",
        side_effect=SQLAlchemyError("private SQL and parameters"),
    )
    with pytest.raises(PersistenceError) as migration_error:
        upgrade_database(engine)
    assert command.called
    assert str(migration_error.value) == "Persistence operation failed"
    assert "private" not in repr(migration_error.value)
    assert migration_error.value.__suppress_context__
    engine.dispose()


@pytest.mark.unit
def test_every_repository_operation_sanitizes_sqlalchemy_failures(
    mocker: MockerFixture,
) -> None:
    engine = mocker.MagicMock()
    users = SqlUserRepository(engine)
    sessions = SqlSessionRepository(engine)
    private_hash = "private-" + "hash"
    user = User(uuid4(), "Private", "private", private_hash, Role.USER)
    now = datetime(2026, 8, 23, tzinfo=UTC)
    session = Session(
        token_digest="e" * 64,
        csrf_digest="f" * 64,
        user_id=user.id,
        auth_version=0,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )
    mocker.patch(
        "md_converter.persistence.sql.DatabaseSession",
        side_effect=SQLAlchemyError("private SQL parameters"),
    )
    operations = (
        lambda: users.get_by_id(user.id),
        lambda: users.get_by_normalized_username(user.normalized_username),
        users.list,
        lambda: users.commit_verified_login(user.id, 0, private_hash),
        lambda: users.update_security(user.id, password_hash=private_hash),
        lambda: sessions.create(session),
        lambda: sessions.get(session.token_digest),
        lambda: sessions.save(session),
        lambda: sessions.revoke(session.token_digest),
        lambda: sessions.revoke_user(user.id),
    )
    for operation in operations:
        with pytest.raises(PersistenceError) as caught:
            operation()
        assert "private" not in repr(caught.value)

    mocker.patch(
        "md_converter.persistence.sql.create_engine",
        side_effect=SQLAlchemyError("private URL"),
    )
    with pytest.raises(PersistenceError) as engine_error:
        create_database_engine("sqlite+pysqlite://")
    assert "private" not in repr(engine_error.value)


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

    job_operations = mocker.patch.object(JOB_REVISION, "op")
    JOB_REVISION.upgrade()
    job_operations.create_table.assert_called_once()
    assert job_operations.create_index.call_count == 5
    JOB_REVISION.downgrade()
    assert job_operations.drop_index.call_count == 5
    job_operations.drop_table.assert_called_once_with("conversion_jobs")
    engine.dispose()


@pytest.mark.unit
def test_alembic_environment_rejects_an_unmanaged_connection(
    mocker: MockerFixture,
) -> None:
    context = mocker.MagicMock()
    context.config.attributes = {"connection": object()}
    with pytest.raises(RuntimeError, match="application-managed"):
        run_migration_environment(context)

    with pytest.raises(ValueError, match="must not be blank"):
        downgrade_database(create_database_engine("sqlite+pysqlite://"), " ")


@pytest.mark.unit
def test_retention_migrations_cover_schema_and_both_immutability_dialects(
    mocker: MockerFixture,
) -> None:
    retention = mocker.patch.object(RETENTION_REVISION, "op")
    batch = retention.batch_alter_table.return_value.__enter__.return_value
    RETENTION_REVISION.upgrade()
    assert retention.add_column.call_count == 2
    assert retention.create_index.call_count == 2
    retention.create_table.assert_called_once()
    RETENTION_REVISION.downgrade()
    assert retention.drop_index.call_count == 2
    assert batch.drop_column.call_count == 2

    immutable = mocker.patch.object(IMMUTABILITY_REVISION, "op")
    immutable.get_bind.return_value.dialect.name = "sqlite"
    IMMUTABILITY_REVISION.upgrade()
    IMMUTABILITY_REVISION.downgrade()
    assert immutable.execute.call_count == 4

    immutable.reset_mock()
    immutable.get_bind.return_value.dialect.name = "postgresql"
    IMMUTABILITY_REVISION.upgrade()
    IMMUTABILITY_REVISION.downgrade()
    assert immutable.execute.call_count == 6

    cleanup_evidence = mocker.patch.object(CLEANUP_EVIDENCE_REVISION, "op")
    cleanup_evidence.get_bind.return_value.dialect.name = "sqlite"
    CLEANUP_EVIDENCE_REVISION.upgrade()
    CLEANUP_EVIDENCE_REVISION.downgrade()
    assert cleanup_evidence.execute.call_count == 3

    cleanup_evidence.reset_mock()
    cleanup_evidence.get_bind.return_value.dialect.name = "postgresql"
    CLEANUP_EVIDENCE_REVISION.upgrade()
    CLEANUP_EVIDENCE_REVISION.downgrade()
    assert cleanup_evidence.execute.call_count == 3
