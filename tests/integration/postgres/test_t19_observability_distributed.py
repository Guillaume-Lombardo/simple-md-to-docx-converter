"""PostgreSQL/RustFS observability and readiness parity."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url

from md_converter.app import create_app
from md_converter.auth.models import Role, User
from md_converter.config import Settings
from md_converter.jobs.models import JobOutput, JobRequest
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.observability import SqlOperationalObserver
from md_converter.persistence.schema import UserRow
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.storage import FilesystemObjectStore
from tests.settings import template_settings
from tests.template_records import publish_template_pair


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_queue_observation_matches_standalone_contract(tmp_path) -> None:
    base_url = make_url(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    schema = f"t19_observability_{uuid4().hex}"
    admin_engine = create_database_engine(base_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(isolated_url)
    try:
        upgrade_database(engine)
        owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
        SqlUserRepository(engine).create(owner)
        template_id, version_id = uuid4(), uuid4()
        publish_template_pair(engine, owner.id, template_id, version_id)
        repository = SqlJobRepository(engine)
        now = datetime(2026, 8, 24, 20, tzinfo=UTC)
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
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_readiness_and_metrics_use_bounded_real_probes() -> None:
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

    database = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    try:
        ready_app = create_app(settings(os.environ["MD_CONVERTER_TEST_S3_BUCKET"]))
        with TestClient(ready_app, base_url="https://testserver") as client:
            assert client.get("/health/ready").json() == {"status": "ready"}
            metrics = client.get("/metrics")
            assert metrics.status_code == 200
            assert "md_converter_queue_depth" in metrics.text
            assert "md_converter_active_jobs" in metrics.text

        unavailable_app = create_app(settings(f"missing-t19-{suffix}"))
        with TestClient(unavailable_app, base_url="https://testserver") as client:
            failed = client.get("/health/ready")
            assert failed.status_code == 503
            assert failed.json()["error"]["code"] == "NOT_READY"
    finally:
        with database.begin() as connection:
            connection.execute(
                delete(UserRow).where(UserRow.normalized_username == username)
            )
        database.dispose()
