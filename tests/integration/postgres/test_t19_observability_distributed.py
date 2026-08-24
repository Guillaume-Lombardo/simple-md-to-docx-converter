"""Isolated PostgreSQL/RustFS observability and readiness parity."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from md_converter.app import create_app
from md_converter.auth.models import (
    AuthenticationAuditContext,
    AuthenticationAuditOperation,
    Role,
    User,
)
from md_converter.config import Settings
from md_converter.jobs.errors import JobRepositoryError
from md_converter.jobs.models import JobOutput, JobRequest
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import downgrade_database, upgrade_database
from md_converter.persistence.observability import (
    SqlAuditReader,
    SqlOperationalObserver,
)
from md_converter.persistence.retention import SqlRetentionRepository
from md_converter.persistence.schema import (
    AuthenticationAuditRow,
    RetentionCleanupRunRow,
    TemplateAuditRow,
)
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.storage import FilesystemObjectStore
from tests.settings import template_settings
from tests.template_records import publish_template_pair


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_queue_observation_matches_standalone_contract(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    try:
        upgrade_database(engine)
        now = datetime(2026, 8, 24, 20, tzinfo=UTC)
        owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
        users = SqlUserRepository(engine)
        users.create(
            owner,
            audit=AuthenticationAuditContext(
                uuid4(), owner.id, AuthenticationAuditOperation.CREATE, now
            ),
        )
        users.update_security(
            owner.id,
            active=False,
            audit=AuthenticationAuditContext(
                uuid4(),
                owner.id,
                AuthenticationAuditOperation.DEACTIVATE,
                now + timedelta(seconds=1),
            ),
        )
        with pytest.raises(KeyError):
            users.create(
                User(uuid4(), "OWNER", "owner", "private-duplicate", Role.USER),
                audit=AuthenticationAuditContext(
                    uuid4(), owner.id, AuthenticationAuditOperation.CREATE, now
                ),
            )
        audits = SqlAuditReader(engine).list_recent(offset=0, limit=10)
        assert [record.operation for record in audits] == [
            "user_deactivate",
            "user_create",
        ]
        template_id, version_id = uuid4(), uuid4()
        publish_template_pair(engine, owner.id, template_id, version_id)
        repository = SqlJobRepository(engine)
        job, _ = JobService(
            repository, FilesystemObjectStore(tmp_path), JobServicePolicy(3_600)
        ).submit(
            JobRequest(
                owner.id,
                b"# private",
                template_id,
                version_id,
                JobOutput.DOCX,
                (("md-converter", "0.1.0"),),
                now,
                "request-distributed",
            ),
            None,
        )
        assert job.correlation_id == "request-distributed"
        queued = SqlOperationalObserver(engine).observe_queue(
            now + timedelta(seconds=4)
        )
        assert (queued.depth, queued.oldest_age_seconds, queued.active_jobs) == (
            1,
            4.0,
            0,
        )
        repository.claim("distributed-worker", now, now + timedelta(seconds=30))
        running = SqlOperationalObserver(engine).observe_queue(now)
        assert (running.depth, running.active_jobs) == (0, 1)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_queue_observation_statement_timeout_is_bounded() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    try:
        upgrade_database(engine)
        with engine.connect() as blocker:
            transaction = blocker.begin()
            blocker.execute(text("LOCK TABLE conversion_jobs IN ACCESS EXCLUSIVE MODE"))
            started = monotonic()
            with pytest.raises(JobRepositoryError):
                SqlOperationalObserver(engine).observe_queue(
                    datetime.now(UTC), timeout_seconds=0.1
                )
            assert monotonic() - started < 1.0
            transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_observation_engine_preserves_isolated_search_path() -> None:
    engine = create_database_engine(
        os.environ["MD_CONVERTER_TEST_POSTGRES_URL"], timeout_seconds=0.5
    )
    try:
        upgrade_database(engine)
        with engine.connect() as connection:
            driver_id = id(connection.connection.driver_connection)
            schema = connection.scalar(text("SELECT current_schema()"))
            assert isinstance(schema, str) and schema.startswith("test_")
            assert connection.scalar(text("SHOW statement_timeout")) == "500ms"
        with engine.connect() as connection:
            assert id(connection.connection.driver_connection) == driver_id
            assert connection.scalar(text("SHOW statement_timeout")) == "500ms"
        assert (
            SqlOperationalObserver(engine).observe_queue(datetime.now(UTC)).depth == 0
        )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_missing_bucket_then_healthy_distributed_contract_is_isolated() -> None:
    suffix = uuid4().hex
    username = f"t19-{suffix}"

    def settings(bucket: str) -> Settings:
        return Settings(
            **template_settings(readiness_timeout_seconds=1.0),
            initial_admin_username=username,
            initial_admin_password="distributed-" + "password",
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="distributed",
            distributed_database_url=os.environ["MD_CONVERTER_TEST_POSTGRES_URL"],
            s3_bucket=bucket,
            s3_endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
            s3_region=os.environ["MD_CONVERTER_TEST_S3_REGION"],
            s3_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
            s3_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
            conversion_upload_max_bytes=128,
            conversion_request_max_bytes=1_024,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=3_600,
        )

    unavailable_app = create_app(settings(f"missing-t19-{suffix}"))
    with TestClient(unavailable_app, base_url="https://testserver") as client:
        failed = client.get("/health/ready")
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "NOT_READY"

    ready_app = create_app(settings(os.environ["MD_CONVERTER_TEST_S3_BUCKET"]))
    with TestClient(ready_app, base_url="https://testserver") as client:
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert (
            ready_app.state.components.authentication.login(
                username, "distributed-" + "password"
            ).user.role
            is Role.ADMIN
        )
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "md_converter_queue_depth" in metrics.text
        assert "md_converter_active_jobs" in metrics.text


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_combined_audit_contract_and_concurrent_cleanup() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    actor = uuid4()
    identifiers = [UUID(int=index) for index in range(1, 6)]
    with engine.begin() as connection:
        connection.execute(
            insert(AuthenticationAuditRow),
            [
                {
                    "id": str(identifiers[index]),
                    "actor_id": str(actor),
                    "owner_id": str(actor),
                    "operation": "user_create",
                    "target_id": str(actor),
                    "auth_version": index,
                    "administrator_intervention": True,
                    "created_at": now + timedelta(seconds=index),
                }
                for index in (0, 2, 4)
            ],
        )
        connection.execute(
            insert(TemplateAuditRow),
            [
                {
                    "id": str(identifiers[index]),
                    "actor_id": str(actor),
                    "owner_id": str(actor),
                    "template_id": str(uuid4()),
                    "operation": "replace",
                    "version_id": None,
                    "administrator_intervention": False,
                    "created_at": now + timedelta(seconds=index),
                }
                for index in (1, 3)
            ],
        )

    reader = SqlAuditReader(engine)
    assert [record.id for record in reader.list_recent(offset=0, limit=3)] == list(
        reversed(identifiers[2:])
    )
    assert [record.id for record in reader.list_recent(offset=2, limit=2)] == [
        identifiers[2],
        identifiers[1],
    ]
    for statement in (
        update(AuthenticationAuditRow)
        .where(AuthenticationAuditRow.id == str(identifiers[0]))
        .values(operation="user_password_reset"),
        delete(AuthenticationAuditRow).where(
            AuthenticationAuditRow.id == str(identifiers[0])
        ),
        update(TemplateAuditRow)
        .where(TemplateAuditRow.id == str(identifiers[1]))
        .values(operation="restore"),
        delete(TemplateAuditRow).where(TemplateAuditRow.id == str(identifiers[1])),
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(statement)

    repository = SqlRetentionRepository(engine)
    assert (
        repository.cleanup_audits(
            cutoff_at=now + timedelta(minutes=1), completed_at=now, limit=1
        )
        == 1
    )
    assert [record.id for record in reader.list_recent(offset=0, limit=10)] == list(
        reversed(identifiers[1:])
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanups = tuple(
            pool.map(
                lambda _: repository.cleanup_audits(
                    cutoff_at=now + timedelta(minutes=1),
                    completed_at=now,
                    limit=2,
                ),
                range(2),
            )
        )
    assert cleanups == (2, 2)
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(AuthenticationAuditRow))
            == 0
        )
        assert (
            connection.scalar(select(func.count()).select_from(TemplateAuditRow)) == 0
        )
        assert (
            connection.scalar(select(func.count()).select_from(RetentionCleanupRunRow))
            == 3
        )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_revision_11_downgrade_removes_authentication_audit() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    assert "authentication_audit_records" in inspect(engine).get_table_names()
    assert "audit_cleanup_guards" in inspect(engine).get_table_names()
    downgrade_database(engine, "20260824_10")
    assert "authentication_audit_records" not in inspect(engine).get_table_names()
    assert "audit_cleanup_guards" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname = 'template_audit_records_immutable_delete' "
                    "AND tgrelid = to_regclass('template_audit_records') "
                    "AND NOT tgisinternal"
                )
            )
            == 0
        )
    engine.dispose()
