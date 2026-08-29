"""Fast in-process tests for SQL repository control flow."""

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from markweave.auth.models import (
    AuthenticationAuditContext,
    AuthenticationAuditOperation,
    Role,
    Session,
    User,
)
from markweave.config import ConfigurationError
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import (
    downgrade_database,
    run_migration_environment,
    upgrade_database,
)
from markweave.persistence.schema import AuthenticationAuditRow
from markweave.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)

REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260823_01_auth_tables"
)
JOB_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260824_03_conversion_jobs"
)
RETENTION_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260824_07_retention_cleanup"
)
IMMUTABILITY_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260824_08_immutable_retention_records"
)
CLEANUP_EVIDENCE_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260824_09_immutable_cleanup_evidence"
)
CORRELATION_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260824_10_job_correlation"
)
AUTH_AUDIT_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260824_11_authentication_audit"
)
JOB_INTEGRITY_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260825_12_job_integrity_metadata"
)
OPTIONAL_TEMPLATE_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260828_13_optional_job_template"
)
PASSWORD_CHANGE_REVISION: Any = importlib.import_module(
    "markweave.persistence.migrations.versions.20260829_14_password_change_required"
)


@pytest.mark.unit
def test_inprocess_sql_repository_control_flow() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    upgrade_database(engine)
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        "audit_cleanup_guards",
        "authentication_audit_records",
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
    job_columns = {
        column["name"] for column in inspect(engine).get_columns("conversion_jobs")
    }
    assert {
        "source_filename",
        "source_kind",
        "source_sha256",
        "source_size",
        "result_manifest_object_id",
    } <= job_columns
    assert "password_change_required" in {
        column["name"] for column in inspect(engine).get_columns("users")
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
def test_password_change_required_migration_has_a_real_downgrade_path() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    assert "password_change_required" in {
        column["name"] for column in inspect(engine).get_columns("users")
    }

    downgrade_database(engine, "20260828_13")
    assert "password_change_required" not in {
        column["name"] for column in inspect(engine).get_columns("users")
    }
    upgrade_database(engine)
    assert "password_change_required" in {
        column["name"] for column in inspect(engine).get_columns("users")
    }
    engine.dispose()


@pytest.mark.unit
def test_password_change_required_revision_is_directly_verifiable(
    mocker: MockerFixture,
) -> None:
    operations = mocker.patch.object(PASSWORD_CHANGE_REVISION, "op")
    batch = operations.batch_alter_table.return_value.__enter__.return_value

    PASSWORD_CHANGE_REVISION.upgrade()
    operations.add_column.assert_called_once()
    table, column = operations.add_column.call_args.args
    assert table == "users"
    assert column.name == "password_change_required"
    assert not column.nullable

    PASSWORD_CHANGE_REVISION.downgrade()
    operations.batch_alter_table.assert_called_once_with("users", recreate="auto")
    batch.drop_column.assert_called_once_with("password_change_required")


@pytest.mark.unit
def test_database_engine_applies_profile_bounded_timeouts(
    mocker: MockerFixture,
) -> None:
    created = mocker.patch("markweave.persistence.sql.create_engine")
    listen = mocker.patch("markweave.persistence.sql.event.listen")

    sqlite_engine = create_database_engine(
        "sqlite+pysqlite:///:memory:", timeout_seconds=0.5
    )
    assert sqlite_engine is created.return_value
    assert created.call_args.kwargs["connect_args"] == {
        "check_same_thread": False,
        "timeout": 0.5,
    }
    listen.assert_called_once()

    create_database_engine(
        "postgresql+psycopg://database/app?options=-csearch_path%3Disolated",
        timeout_seconds=0.5,
    )
    assert created.call_args.args[0].query["options"] == "-csearch_path=isolated"
    assert created.call_args.kwargs["connect_args"] == {"connect_timeout": 1}
    assert created.call_args.kwargs["pool_timeout"] == 0.5
    timeout_listener = listen.call_args.args[2]
    postgres_connection = mocker.Mock()
    postgres_connection.autocommit = False
    timeout_listener(postgres_connection, mocker.Mock())
    postgres_cursor = postgres_connection.cursor.return_value
    postgres_cursor.execute.assert_called_once_with(
        "SELECT set_config('statement_timeout', %s, false)", ("500ms",)
    )
    postgres_cursor.close.assert_called_once_with()
    assert postgres_connection.autocommit is False
    with pytest.raises(ValueError, match="timeout"):
        create_database_engine("sqlite+pysqlite:///:memory:", timeout_seconds=0)


@pytest.mark.unit
def test_sqlite_account_audits_are_atomic_immutable_and_content_free() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    admin = users.bootstrap_admin("Admin", "admin", "hash:private-admin")
    user = User(uuid4(), "Alice", "alice", "hash:private-user", Role.USER)
    now = datetime(2026, 8, 24, tzinfo=UTC)

    def audit(operation: AuthenticationAuditOperation) -> AuthenticationAuditContext:
        return AuthenticationAuditContext(uuid4(), admin.id, operation, now)

    users.create(user, audit=audit(AuthenticationAuditOperation.CREATE))
    with pytest.raises(KeyError):
        users.create(
            User(uuid4(), "ALICE", "alice", "hash:must-not-leak", Role.USER),
            audit=audit(AuthenticationAuditOperation.CREATE),
        )
    assert (
        users.update_security(
            uuid4(),
            active=False,
            audit=audit(AuthenticationAuditOperation.DEACTIVATE),
        )
        is None
    )
    users.update_security(
        user.id,
        password_hash="hash:" + "new-private-value",
        audit=audit(AuthenticationAuditOperation.RESET_PASSWORD),
    )
    with engine.connect() as connection:
        rows = tuple(
            connection.execute(
                select(AuthenticationAuditRow).order_by(
                    AuthenticationAuditRow.created_at
                )
            ).mappings()
        )
    assert {row["operation"] for row in rows} == {
        "bootstrap_admin_create",
        "user_create",
        "user_password_reset",
    }
    reset = next(row for row in rows if row["operation"] == "user_password_reset")
    assert reset["auth_version"] == 1
    assert "private" not in repr(rows)
    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(update(AuthenticationAuditRow).values(operation="tampered"))
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
def test_postgresql_user_update_helper_keeps_returning(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "postgresql"
    repository = SqlUserRepository(engine)
    database = mocker.Mock()
    statement = mocker.Mock()
    expected = mocker.Mock()
    database.execute.return_value.scalar_one_or_none.return_value = expected

    assert repository._update_user_row(database, statement, "user-id") is expected

    statement.returning.assert_called_once()
    database.execute.assert_called_once_with(statement.returning.return_value)


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
        "markweave.persistence.migrations.command.upgrade",
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
        "markweave.persistence.sql.DatabaseSession",
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
        "markweave.persistence.sql.create_engine",
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

    auth_audit = mocker.patch.object(AUTH_AUDIT_REVISION, "op")
    auth_audit.get_bind.return_value.dialect.name = "sqlite"
    AUTH_AUDIT_REVISION.upgrade()
    AUTH_AUDIT_REVISION.downgrade()
    assert auth_audit.create_table.call_count == 2
    assert auth_audit.create_index.call_count == 2
    assert auth_audit.execute.call_count == 6
    assert [call.args[0] for call in auth_audit.drop_table.call_args_list] == [
        "authentication_audit_records",
        "audit_cleanup_guards",
    ]

    auth_audit.reset_mock()
    auth_audit.get_bind.return_value.dialect.name = "postgresql"
    AUTH_AUDIT_REVISION.upgrade()
    AUTH_AUDIT_REVISION.downgrade()
    assert auth_audit.execute.call_count == 8

    cleanup_evidence.reset_mock()
    cleanup_evidence.get_bind.return_value.dialect.name = "postgresql"
    CLEANUP_EVIDENCE_REVISION.upgrade()
    CLEANUP_EVIDENCE_REVISION.downgrade()
    assert cleanup_evidence.execute.call_count == 3

    correlation = mocker.patch.object(CORRELATION_REVISION, "op")
    CORRELATION_REVISION.upgrade()
    correlation.add_column.assert_called_once()
    correlation.execute.assert_called_once_with(
        "UPDATE conversion_jobs SET correlation_id = id WHERE correlation_id IS NULL"
    )
    CORRELATION_REVISION.downgrade()
    correlation.drop_column.assert_called_once_with("conversion_jobs", "correlation_id")


@pytest.mark.unit
def test_job_integrity_downgrade_uses_batch_table_copy(mocker: MockerFixture) -> None:
    integrity = mocker.patch.object(JOB_INTEGRITY_REVISION, "op")
    batch = integrity.batch_alter_table.return_value.__enter__.return_value
    JOB_INTEGRITY_REVISION.downgrade()
    assert batch.drop_column.call_count == 5
    integrity.drop_column.assert_not_called()


@pytest.mark.unit
def test_job_integrity_sqlite_downgrade_preserves_referencing_triggers(
    mocker: MockerFixture,
) -> None:
    integrity = mocker.patch.object(JOB_INTEGRITY_REVISION, "op")
    bind = mocker.MagicMock()
    bind.dialect.name = "sqlite"
    trigger_rows = mocker.Mock()
    trigger_rows.tuples.return_value = (
        ("conversion_guard", "CREATE TRIGGER conversion_guard AFTER INSERT ON users"),
        ("job_guard", "CREATE TRIGGER job_guard AFTER INSERT ON conversion_jobs"),
    )
    bind.execute.return_value = trigger_rows
    integrity.get_bind.return_value = bind

    JOB_INTEGRITY_REVISION.downgrade()

    assert integrity.batch_alter_table.call_args.kwargs["recreate"] == "always"
    assert bind.execute.call_count == 5
    executed = [str(call.args[0]) for call in bind.execute.call_args_list]
    assert 'DROP TRIGGER "conversion_guard"' in executed
    assert 'DROP TRIGGER "job_guard"' in executed
    assert executed[-2:] == [
        "CREATE TRIGGER conversion_guard AFTER INSERT ON users",
        "CREATE TRIGGER job_guard AFTER INSERT ON conversion_jobs",
    ]


@pytest.mark.unit
def test_optional_template_postgresql_trigger_only_revalidates_changed_pair(
    mocker: MockerFixture,
) -> None:
    operations = mocker.patch.object(OPTIONAL_TEMPLATE_REVISION, "op")
    operations.get_bind.return_value.dialect.name = "postgresql"

    OPTIONAL_TEMPLATE_REVISION._create_conversion_integrity()

    statement = operations.execute.call_args.args[0]
    assert "TG_OP = 'UPDATE'" in statement
    assert "IS NOT DISTINCT FROM" in statement
    assert "NEW.template_id IS NULL AND NEW.template_version_id IS NULL" in statement


@pytest.mark.unit
def test_optional_template_sqlite_upgrade_and_downgrade_rebuild_integrity(
    mocker: MockerFixture,
) -> None:
    operations = mocker.patch.object(OPTIONAL_TEMPLATE_REVISION, "op")
    bind = mocker.MagicMock()
    bind.dialect.name = "sqlite"
    bind.execute.return_value.first.return_value = None
    operations.get_bind.return_value = bind

    OPTIONAL_TEMPLATE_REVISION.upgrade()
    upgrade_batch = operations.batch_alter_table.return_value.__enter__.return_value
    assert upgrade_batch.alter_column.call_count == 2
    upgrade_batch.create_check_constraint.assert_called_once()
    assert operations.batch_alter_table.call_args.kwargs["recreate"] == "always"

    operations.reset_mock()
    operations.get_bind.return_value = bind
    OPTIONAL_TEMPLATE_REVISION.downgrade()
    downgrade_batch = operations.batch_alter_table.return_value.__enter__.return_value
    downgrade_batch.drop_constraint.assert_called_once_with(
        "ck_conversion_jobs_template_pair", type_="check"
    )
    assert downgrade_batch.alter_column.call_count == 2
    assert operations.batch_alter_table.call_args.kwargs["recreate"] == "always"


@pytest.mark.unit
def test_optional_template_downgrade_rejects_template_free_jobs(
    mocker: MockerFixture,
) -> None:
    operations = mocker.patch.object(OPTIONAL_TEMPLATE_REVISION, "op")
    bind = mocker.MagicMock()
    bind.execute.return_value.first.return_value = (1,)
    operations.get_bind.return_value = bind

    with pytest.raises(RuntimeError, match="template-free conversion jobs exist"):
        OPTIONAL_TEMPLATE_REVISION.downgrade()
    operations.batch_alter_table.assert_not_called()


@pytest.mark.unit
def test_optional_template_postgresql_legacy_trigger_revalidates_changed_pair(
    mocker: MockerFixture,
) -> None:
    operations = mocker.patch.object(OPTIONAL_TEMPLATE_REVISION, "op")
    operations.get_bind.return_value.dialect.name = "postgresql"

    OPTIONAL_TEMPLATE_REVISION._create_legacy_conversion_integrity()

    statement = operations.execute.call_args.args[0]
    assert "TG_OP = 'INSERT'" in statement
    assert "IS DISTINCT FROM" in statement
