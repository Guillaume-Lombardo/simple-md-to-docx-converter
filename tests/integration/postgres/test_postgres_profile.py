"""Real PostgreSQL authentication repository contract."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, text

from md_converter.app import create_app
from md_converter.auth.models import Role, Session, User
from md_converter.config import Settings
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import (
    POSTGRES_MIGRATION_LOCK,
    upgrade_database,
)
from md_converter.persistence.schema import SessionRow, UserRow
from md_converter.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
)
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
        connection.execute(text("DROP TABLE IF EXISTS sessions CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))

    with ThreadPoolExecutor(max_workers=4) as executor:
        migrations = [executor.submit(upgrade_database, engine) for _ in range(4)]
        for migration in migrations:
            migration.result(timeout=10)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "sessions",
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
    )
    app = create_app(settings)
    assert app.state.components.readiness.is_ready()
    assert (
        app.state.components.authentication.login(
            "admin", "admin-" + "password"
        ).user.role.value
        == "admin"
    )
