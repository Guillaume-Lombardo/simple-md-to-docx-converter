"""PostgreSQL admission serialization and short-load coverage for T18."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import boto3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from md_converter.app import create_app
from md_converter.auth.models import Role, User
from md_converter.config import Settings
from md_converter.jobs.errors import (
    JobConflictError,
    JobQueueCapacityExceededError,
    JobUserQuotaExceededError,
)
from md_converter.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from md_converter.jobs.policy import JobAdmissionPolicy
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.malware import TrustingUploadScanner
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import (
    ConversionJobRow,
    TemplateAuditRow,
    TemplateRow,
    UserRow,
)
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
    S3ObjectStore,
)
from tests.settings import template_settings
from tests.template_records import publish_template_pair

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def _submit_http(
    client: TestClient,
    csrf: str,
    template_id: UUID,
    version_id: UUID,
    key: str,
):
    return client.post(
        "/api/v1/conversions",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        files={"source": ("source.md", f"# {key}".encode(), "text/markdown")},
        data={
            "template_id": str(template_id),
            "template_version_id": str(version_id),
            "output": "docx",
        },
    )


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_asgi_enforces_owner_and_global_admission(  # noqa: PLR0915
    request: pytest.FixtureRequest,
) -> None:
    unique = uuid4().hex
    admin_username = f"t18-api-{unique}"
    password = "admin-" + "password"
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    app = create_app(
        Settings(
            **template_settings(
                job_active_limit_per_user=1,
                job_global_queue_capacity=2,
            ),
            initial_admin_username=admin_username,
            initial_admin_password=password,
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="distributed",
            distributed_database_url=database_url,
            s3_bucket=os.environ["MD_CONVERTER_TEST_S3_BUCKET"],
            s3_endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
            s3_region=os.environ["MD_CONVERTER_TEST_S3_REGION"],
            s3_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
            s3_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
            conversion_upload_max_bytes=128,
            conversion_request_max_bytes=2_000,
            conversion_retry_after_seconds=9,
            job_result_retention_seconds=60,
        ),
        scanner=TrustingUploadScanner(),
    )
    engine = create_database_engine(database_url)
    owner_ids: list[UUID] = []
    job_ids: list[UUID] = []
    template_id, version_id = uuid4(), uuid4()

    def cleanup() -> None:
        repository = SqlJobRepository(engine)
        for job_id in job_ids:
            job = repository.get(job_id)
            if job is not None:
                app.state.components.object_store.delete(
                    ObjectKey(ObjectScope.UPLOAD, job.owner_id, job.source_object_id)
                )
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(
                    ConversionJobRow.owner_id.in_(str(owner) for owner in owner_ids)
                )
            )
            connection.execute(
                delete(TemplateAuditRow).where(
                    TemplateAuditRow.template_id == str(template_id)
                )
            )
            connection.execute(
                delete(TemplateRow).where(TemplateRow.id == str(template_id))
            )
            connection.execute(
                delete(UserRow).where(UserRow.id.in_(str(owner) for owner in owner_ids))
            )
        engine.dispose()

    request.addfinalizer(cleanup)
    with TestClient(app, base_url="https://testserver") as client:
        admin_login = client.post(
            "/api/v1/login",
            json={"username": admin_username, "password": password},
        ).json()
        admin_id = UUID(admin_login["user"]["id"])
        owner_ids.append(admin_id)
        admin_csrf = admin_login["csrf_token"]
        publish_template_pair(engine, admin_id, template_id, version_id)
        for username in (f"alice-{unique}", f"bob-{unique}"):
            created = client.post(
                "/api/v1/admin/users",
                headers={"X-CSRF-Token": admin_csrf},
                json={"username": username, "password": f"{username}-password"},
            )
            assert created.status_code == 201
            owner_ids.append(UUID(created.json()["id"]))

        first = _submit_http(client, admin_csrf, template_id, version_id, "admin-one")
        assert first.status_code == 202
        job_ids.append(UUID(first.json()["id"]))
        owner_full = _submit_http(
            client, admin_csrf, template_id, version_id, "admin-two"
        )
        assert owner_full.status_code == 429
        assert owner_full.headers["Retry-After"] == "9"

        alice_name = f"alice-{unique}"
        alice_login = client.post(
            "/api/v1/login",
            json={"username": alice_name, "password": f"{alice_name}-password"},
        ).json()
        alice = _submit_http(
            client,
            alice_login["csrf_token"],
            template_id,
            version_id,
            "alice-one",
        )
        assert alice.status_code == 202
        job_ids.append(UUID(alice.json()["id"]))
        bob_name = f"bob-{unique}"
        bob_login = client.post(
            "/api/v1/login",
            json={"username": bob_name, "password": f"{bob_name}-password"},
        ).json()
        global_full = _submit_http(
            client,
            bob_login["csrf_token"],
            template_id,
            version_id,
            "bob-one",
        )
        assert global_full.status_code == 503
        assert global_full.headers["Retry-After"] == "9"
        assert global_full.json()["error"]["code"] == (
            "CONVERSION_QUEUE_CAPACITY_EXCEEDED"
        )
        admin_login = client.post(
            "/api/v1/login",
            json={"username": admin_username, "password": password},
        ).json()
        replay = _submit_http(
            client, admin_login["csrf_token"], template_id, version_id, "admin-one"
        )
        assert replay.status_code == 202
        assert replay.json()["id"] == first.json()["id"]
        assert app.state.components.job_policies.admission == JobAdmissionPolicy(1, 2)


class FailOnceResultDeleteStore:
    """Real S3 delegate with one injected result-deletion failure."""

    def __init__(self, delegate: ObjectStore) -> None:
        self._delegate = delegate
        self.failed = False

    def put(self, key: ObjectKey, content: bytes) -> None:
        self._delegate.put(key, content)

    def get(self, key: ObjectKey) -> bytes:
        return self._delegate.get(key)

    def delete(self, key: ObjectKey) -> None:
        if key.scope is ObjectScope.RESULT and not self.failed:
            self.failed = True
            raise ObjectStoreError("Object storage operation failed")
        self._delegate.delete(key)

    def exists(self, key: ObjectKey) -> bool:
        return self._delegate.exists(key)

    def is_ready(self) -> bool:
        return self._delegate.is_ready()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.slow
def test_postgresql_short_load_serializes_global_capacity(tmp_path: Path) -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    suffix = uuid4().hex
    users = tuple(
        User(uuid4(), f"Load {index}", f"t18-{suffix}-{index}", "hash", Role.USER)
        for index in range(12)
    )
    template_id = uuid4()
    version_id = uuid4()
    accounts = SqlUserRepository(engine)
    for user in users:
        accounts.create(user)
    publish_template_pair(engine, users[0].id, template_id, version_id)
    repository = SqlJobRepository(engine, JobAdmissionPolicy(1, 3))
    service = JobService(
        repository, FilesystemObjectStore(tmp_path), JobServicePolicy(60)
    )

    def submit(user: User) -> bool:
        try:
            service.submit(
                JobRequest(
                    user.id,
                    user.normalized_username.encode(),
                    template_id,
                    version_id,
                    JobOutput.DOCX,
                    (("md-converter", "0.1.0"),),
                    NOW,
                ),
                user.normalized_username,
            )
        except JobQueueCapacityExceededError:
            return False
        return True

    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            admitted = tuple(executor.map(submit, users))
        assert sum(admitted) == 3
        admitted_user = next(
            user for user, accepted in zip(users, admitted, strict=True) if accepted
        )
        available_user = next(
            user for user, accepted in zip(users, admitted, strict=True) if not accepted
        )

        same_owner_repository = SqlJobRepository(engine, JobAdmissionPolicy(1, 10))
        same_owner_service = JobService(
            same_owner_repository,
            FilesystemObjectStore(tmp_path),
            JobServicePolicy(60),
        )

        def submit_same_owner(index: int) -> bool:
            try:
                same_owner_service.submit(
                    JobRequest(
                        available_user.id,
                        f"same owner {index}".encode(),
                        template_id,
                        version_id,
                        JobOutput.DOCX,
                        (("md-converter", "0.1.0"),),
                        NOW,
                    ),
                    f"same-owner-{index}",
                )
            except JobUserQuotaExceededError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=8) as executor:
            assert sum(executor.map(submit_same_owner, range(8))) == 1

        replay_request = JobRequest(
            admitted_user.id,
            admitted_user.normalized_username.encode(),
            template_id,
            version_id,
            JobOutput.DOCX,
            (("md-converter", "0.1.0"),),
            NOW,
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            replays = tuple(
                executor.map(
                    lambda _index: service.submit(
                        replay_request, admitted_user.normalized_username
                    ),
                    range(8),
                )
            )
        assert len({job.id for job, replayed in replays if replayed}) == 1
        with pytest.raises(JobConflictError):
            service.submit(
                JobRequest(
                    admitted_user.id,
                    b"changed",
                    template_id,
                    version_id,
                    JobOutput.DOCX,
                    (("md-converter", "0.1.0"),),
                    NOW,
                ),
                admitted_user.normalized_username,
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(
                    ConversionJobRow.owner_id.in_(str(user.id) for user in users)
                )
            )
            connection.execute(
                delete(TemplateRow).where(TemplateRow.id == str(template_id))
            )
            connection.execute(
                delete(UserRow).where(UserRow.id.in_(str(user.id) for user in users))
            )
        engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_retention_cleanup_removes_rustfs_source() -> None:  # noqa: PLR0915
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    suffix = uuid4().hex
    owner = User(uuid4(), "Cleanup", f"t18-cleanup-{suffix}", "hash", Role.USER)
    template_id = uuid4()
    version_id = uuid4()
    SqlUserRepository(engine).create(owner)
    publish_template_pair(engine, owner.id, template_id, version_id)
    repository = SqlJobRepository(engine, JobAdmissionPolicy(1, 2))
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MD_CONVERTER_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
    )
    objects = S3ObjectStore(client, os.environ["MD_CONVERTER_TEST_S3_BUCKET"])
    service = JobService(repository, objects, JobServicePolicy(10))
    queued, _ = service.submit(
        JobRequest(
            owner.id,
            b"private",
            template_id,
            version_id,
            JobOutput.DOCX,
            (("md-converter", "0.1.0"),),
            NOW,
        ),
        f"cleanup-{suffix}",
    )
    source_key = ObjectKey(ObjectScope.UPLOAD, owner.id, queued.source_object_id)

    class ResultProcessor:
        def process(self, *_args: object, **_kwargs: object) -> JobProcessResult:
            return JobProcessResult(b"result")

    processing_worker = ConversionWorker(
        worker_id=f"cleanup-{suffix}",
        runtime=WorkerRuntime(
            repository,
            objects,
            ResultProcessor(),
            lambda: NOW,
        ),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    failing_store = FailOnceResultDeleteStore(objects)
    cleanup_worker = ConversionWorker(
        worker_id=f"cleanup-reclaimer-{suffix}",
        runtime=WorkerRuntime(
            repository,
            failing_store,
            ResultProcessor(),
            lambda: NOW + timedelta(seconds=11),
        ),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    try:
        assert objects.exists(source_key)
        assert processing_worker.run_once()
        succeeded = repository.get(queued.id)
        assert succeeded is not None and succeeded.result_object_id is not None
        result_key = ObjectKey(ObjectScope.RESULT, owner.id, succeeded.result_object_id)
        assert objects.exists(result_key)
        with pytest.raises(ObjectStoreError):
            cleanup_worker.cleanup(limit=1)
        assert failing_store.failed
        assert not objects.exists(source_key)
        assert objects.exists(result_key)
        with Session(engine) as database:
            row = database.get(ConversionJobRow, str(queued.id))
            assert row is not None and row.cleanup_token is not None
            stale_token = UUID(row.cleanup_token)
        reclaimed = repository.expire_terminal(
            f"cleanup-retry-{suffix}",
            NOW + timedelta(seconds=17),
            NOW + timedelta(seconds=22),
            1,
        )
        assert len(reclaimed) == 1 and reclaimed[0].cleanup_token != stale_token
        assert not repository.complete_cleanup(queued.id, stale_token)
        for object_id in reclaimed[0].result_object_ids:
            objects.delete(ObjectKey(ObjectScope.RESULT, owner.id, object_id))
        assert repository.complete_cleanup(queued.id, reclaimed[0].cleanup_token)
        expired = repository.get(queued.id)
        assert expired is not None and expired.state is JobState.EXPIRED
        assert not objects.exists(source_key)
        assert not objects.exists(result_key)
    finally:
        objects.delete(source_key)
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(
                    ConversionJobRow.owner_id == str(owner.id)
                )
            )
            connection.execute(
                delete(TemplateRow).where(TemplateRow.id == str(template_id))
            )
            connection.execute(delete(UserRow).where(UserRow.id == str(owner.id)))
        engine.dispose()
