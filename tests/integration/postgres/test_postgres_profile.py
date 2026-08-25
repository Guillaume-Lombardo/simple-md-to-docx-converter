"""Real PostgreSQL authentication repository contract."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from markweave.app import create_app
from markweave.auth.models import Role, Session, User
from markweave.config import Settings
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import (
    POSTGRES_MIGRATION_LOCK,
    downgrade_database,
    upgrade_database,
)
from markweave.persistence.schema import SessionRow, UserRow
from markweave.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
)
from markweave.persistence.templates import SqlTemplateCatalogRepository
from markweave.templates.models import TemplateSearch
from tests.settings import template_settings
from tests.storage_contracts import exercise_auth_repository_contract


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_authentication_repository_contract() -> None:
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    with engine.begin() as connection:
        connection.execute(delete(SessionRow))
        connection.execute(delete(UserRow))
    exercise_auth_repository_contract(
        SqlUserRepository(engine), SqlSessionRepository(engine)
    )
    assert DatabaseReadinessProbe(engine).is_ready()
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_failed_transaction_rolls_back_and_repository_recovers() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    first = User(uuid4(), "Conflict", "conflict", "hash:first", Role.USER)
    users.create(first)
    with pytest.raises(KeyError):
        users.create(User(uuid4(), "CONFLICT", "conflict", "hash:second", Role.USER))
    recovered = User(uuid4(), "Recovered", "recovered", "hash:third", Role.USER)
    users.create(recovered)
    assert users.get_by_id(recovered.id) == recovered
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_foreign_key_failure_and_user_delete_cascade() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    sessions = SqlSessionRepository(engine)
    user = User(uuid4(), "Cascade PG", f"cascade-pg-{uuid4()}", "hash:pg", Role.USER)
    users.create(user)
    now = datetime(2026, 8, 23, tzinfo=UTC)
    session = Session(
        token_digest=uuid4().hex * 2,
        csrf_digest=uuid4().hex * 2,
        user_id=user.id,
        auth_version=0,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )
    sessions.create(session)
    missing_digest = uuid4().hex * 2
    with pytest.raises(PersistenceError) as caught:
        sessions.create(
            Session(
                token_digest=missing_digest,
                csrf_digest=uuid4().hex * 2,
                user_id=uuid4(),
                auth_version=0,
                created_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(minutes=30),
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    assert missing_digest not in repr(caught.value)
    with engine.begin() as connection:
        connection.execute(delete(UserRow).where(UserRow.id == str(user.id)))
    assert sessions.get(session.token_digest) is None
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_concurrent_first_migrations_and_advisory_lock() -> None:
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS retention_cleanup_runs CASCADE"))
        connection.execute(
            text("DROP TABLE IF EXISTS authentication_audit_records CASCADE")
        )
        connection.execute(text("DROP TABLE IF EXISTS audit_cleanup_guards CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS template_audit_records CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS template_versions CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS conversion_jobs CASCADE"))
        connection.execute(
            text("DROP TABLE IF EXISTS system_template_selection CASCADE")
        )
        connection.execute(text("DROP TABLE IF EXISTS template_preferences CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS templates CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS sessions CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        connection.execute(
            text("DROP FUNCTION IF EXISTS reject_template_owner_change()")
        )
        connection.execute(
            text("DROP FUNCTION IF EXISTS reject_template_version_change()")
        )
        connection.execute(
            text("DROP FUNCTION IF EXISTS reject_immutable_retention_update() CASCADE")
        )
        connection.execute(
            text("DROP FUNCTION IF EXISTS reject_unauthorized_audit_delete() CASCADE")
        )
        for statement in (
            "DROP FUNCTION IF EXISTS enforce_template_version_integrity()",
            "DROP FUNCTION IF EXISTS enforce_template_current_integrity()",
            "DROP FUNCTION IF EXISTS enforce_conversion_template_integrity()",
        ):
            connection.execute(text(statement))

    with ThreadPoolExecutor(max_workers=4) as executor:
        migrations = [executor.submit(upgrade_database, engine) for _ in range(4)]
        for migration in migrations:
            migration.result(timeout=10)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "audit_cleanup_guards",
        "authentication_audit_records",
        "sessions",
        "system_template_selection",
        "template_audit_records",
        "template_preferences",
        "retention_cleanup_runs",
        "template_versions",
        "templates",
        "users",
    }

    with ThreadPoolExecutor(max_workers=1) as executor:
        with engine.begin() as blocker:
            blocker.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": POSTGRES_MIGRATION_LOCK},
            )
            waiting_migration = executor.submit(upgrade_database, engine)
            deadline = time.monotonic() + 5
            waiting = 0
            while waiting == 0 and time.monotonic() < deadline:
                waiting = blocker.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND NOT granted"
                    )
                )
                if waiting == 0:
                    time.sleep(0.02)
            assert waiting == 1
        waiting_migration.result(timeout=10)
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_cleanup_evidence_trigger_has_a_real_downgrade_path() -> None:
    base_url = make_url(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    schema = f"retention_migration_{uuid4().hex}"
    admin_engine = create_database_engine(base_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(isolated_url)
    report_id = str(uuid4())
    try:
        upgrade_database(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO retention_cleanup_runs "
                    "(id, kind, cutoff_at, removed_count, completed_at) "
                    "VALUES (:id, 'audit', :now, 0, :now)"
                ),
                {"id": report_id, "now": datetime.now(UTC)},
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("DELETE FROM retention_cleanup_runs WHERE id = :id"),
                {"id": report_id},
            )

        downgrade_database(engine, "20260824_08")

        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("DELETE FROM retention_cleanup_runs WHERE id = :id"),
                    {"id": report_id},
                ).rowcount
                == 1
            )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_real_database_outage_fails_readiness_and_operations() -> None:
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    target = create_database_engine(database_url)
    target_database = target.url.database
    assert target_database is not None
    admin_url = target.url.set(database="postgres")
    target.dispose()
    admin = create_database_engine(admin_url).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    quoted_database = admin.dialect.identifier_preparer.quote(target_database)
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(
                f"ALTER DATABASE {quoted_database} WITH ALLOW_CONNECTIONS false"
            )
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": target_database},
            )
        unavailable = create_database_engine(database_url)
        assert not DatabaseReadinessProbe(unavailable).is_ready()
        with pytest.raises(PersistenceError) as caught:
            SqlUserRepository(unavailable).list()
        assert target_database not in repr(caught.value)
        with pytest.raises(PersistenceError) as template_error:
            SqlTemplateCatalogRepository(unavailable).search(
                TemplateSearch(), viewer_id=uuid4(), viewer_is_admin=False
            )
        assert target_database not in repr(template_error.value)
        unavailable.dispose()
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(
                f"ALTER DATABASE {quoted_database} WITH ALLOW_CONNECTIONS true"
            )
        admin.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_profile_wires_postgresql_and_s3_readiness() -> None:
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    with engine.begin() as connection:
        connection.execute(delete(SessionRow))
        connection.execute(delete(UserRow))
    engine.dispose()

    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="distributed",
        distributed_database_url=database_url,
        s3_bucket=os.environ["MD_CONVERTER_TEST_S3_BUCKET"],
        s3_endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
        s3_region=os.environ["MD_CONVERTER_TEST_S3_REGION"],
        s3_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    app = create_app(settings)
    assert app.state.components.readiness.is_ready()
    assert (
        app.state.components.authentication.login(
            "admin", "admin-" + "password"
        ).user.role.value
        == "admin"
    )
