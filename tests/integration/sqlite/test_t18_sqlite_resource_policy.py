"""SQLite/filesystem admission, retention, and short-load coverage for T18."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from markweave.app import create_app
from markweave.auth.models import Role, User
from markweave.config import Settings
from markweave.jobs.errors import (
    JobConflictError,
    JobQueueCapacityExceededError,
    JobUserQuotaExceededError,
)
from markweave.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from markweave.malware import TrustingUploadScanner
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import ConversionJobRow
from markweave.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)
from tests.job_repository_contracts import TEMPLATE_ID, TEMPLATE_VERSION_ID
from tests.settings import template_settings
from tests.template_records import publish_template_pair

NOW = datetime(2026, 8, 24, tzinfo=UTC)
COMPONENTS = (("md-converter", "0.1.0"),)


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
def test_standalone_asgi_enforces_owner_and_global_admission(tmp_path: Path) -> None:
    password = "admin-" + "password"
    app = create_app(
        Settings(
            **template_settings(
                job_active_limit_per_user=1,
                job_global_queue_capacity=2,
            ),
            initial_admin_username="admin",
            initial_admin_password=password,
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="standalone",
            standalone_data_directory=tmp_path,
            conversion_upload_max_bytes=128,
            conversion_request_max_bytes=2_000,
            conversion_retry_after_seconds=7,
            job_result_retention_seconds=60,
        ),
        scanner=TrustingUploadScanner(),
    )
    with TestClient(app, base_url="https://testserver") as client:
        admin_login = client.post(
            "/api/v1/login", json={"username": "admin", "password": password}
        ).json()
        admin_id = UUID(admin_login["user"]["id"])
        admin_csrf = admin_login["csrf_token"]
        template_id, version_id = uuid4(), uuid4()
        engine = create_database_engine(standalone_database_url(tmp_path))
        publish_template_pair(engine, admin_id, template_id, version_id)
        for username in ("alice", "bob"):
            response = client.post(
                "/api/v1/admin/users",
                headers={"X-CSRF-Token": admin_csrf},
                json={"username": username, "password": f"{username}-password"},
            )
            assert response.status_code == 201

        first = _submit_http(client, admin_csrf, template_id, version_id, "admin-one")
        assert first.status_code == 202
        owner_full = _submit_http(
            client, admin_csrf, template_id, version_id, "admin-two"
        )
        assert owner_full.status_code == 429
        assert owner_full.headers["Retry-After"] == "7"
        assert owner_full.json()["error"]["code"] == ("CONVERSION_USER_QUOTA_EXCEEDED")

        alice_login = client.post(
            "/api/v1/login",
            json={"username": "alice", "password": "alice-password"},
        ).json()
        assert (
            _submit_http(
                client,
                alice_login["csrf_token"],
                template_id,
                version_id,
                "alice-one",
            ).status_code
            == 202
        )
        bob_login = client.post(
            "/api/v1/login", json={"username": "bob", "password": "bob-password"}
        ).json()
        global_full = _submit_http(
            client,
            bob_login["csrf_token"],
            template_id,
            version_id,
            "bob-one",
        )
        assert global_full.status_code == 503
        assert global_full.headers["Retry-After"] == "7"
        assert global_full.json()["error"]["code"] == (
            "CONVERSION_QUEUE_CAPACITY_EXCEEDED"
        )
        metrics = client.get("/metrics").text
        assert 'md_converter_job_saturation_total{scope="owner"} 1' in metrics
        assert 'md_converter_job_saturation_total{scope="global"} 1' in metrics
        admin_login = client.post(
            "/api/v1/login", json={"username": "admin", "password": password}
        ).json()
        replay = _submit_http(
            client, admin_login["csrf_token"], template_id, version_id, "admin-one"
        )
        assert replay.status_code == 202
        assert replay.json()["id"] == first.json()["id"]
        assert app.state.components.job_policies.admission == JobAdmissionPolicy(1, 2)
        engine.dispose()


class FailOnceResultDeleteStore:
    """Real filesystem delegate with one injected result-deletion failure."""

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


def _request(owner_id: UUID, source: bytes, now: datetime = NOW) -> JobRequest:
    return JobRequest(
        owner_id,
        source,
        TEMPLATE_ID,
        TEMPLATE_VERSION_ID,
        JobOutput.DOCX,
        COMPONENTS,
        now,
    )


def _runtime(
    tmp_path: Path, *, owner_count: int, capacity: int
) -> tuple[Engine, SqlJobRepository, FilesystemObjectStore, tuple[User, ...]]:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = tuple(
        User(uuid4(), f"Owner {index}", f"owner-{index}", "hash", Role.USER)
        for index in range(owner_count)
    )
    repository_users = SqlUserRepository(engine)
    for owner in users:
        repository_users.create(owner)
    publish_template_pair(engine, users[0].id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
    return (
        engine,
        SqlJobRepository(engine, JobAdmissionPolicy(1, capacity)),
        FilesystemObjectStore(tmp_path),
        users,
    )


@pytest.mark.integration
def test_sqlite_admission_is_owner_scoped_global_and_idempotent(tmp_path: Path) -> None:
    engine, repository, objects, owners = _runtime(tmp_path, owner_count=3, capacity=2)
    service = JobService(repository, objects, JobServicePolicy(10))
    first, replayed = service.submit(_request(owners[0].id, b"# one"), "one")
    assert not replayed
    assert service.submit(_request(owners[0].id, b"# one"), "one") == (first, True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        replays = tuple(
            executor.map(
                lambda _index: service.submit(_request(owners[0].id, b"# one"), "one"),
                range(8),
            )
        )
    assert {job.id for job, replayed in replays if replayed} == {first.id}
    with pytest.raises(JobConflictError):
        service.submit(_request(owners[0].id, b"# changed"), "one")
    with pytest.raises(JobUserQuotaExceededError):
        service.submit(_request(owners[0].id, b"# second"), "second")
    service.submit(_request(owners[1].id, b"# other"), "other")
    with pytest.raises(JobQueueCapacityExceededError):
        service.submit(_request(owners[2].id, b"# full"), "full")

    cancelled = service.cancel(
        first.id, actor_id=owners[0].id, actor_is_admin=False, now=NOW
    )
    assert cancelled.state is JobState.CANCELLED
    replacement, _ = service.submit(
        _request(owners[0].id, b"# replacement"), "replacement"
    )
    assert replacement.state is JobState.QUEUED
    engine.dispose()


@pytest.mark.integration
@pytest.mark.slow
def test_sqlite_concurrent_same_owner_never_overshoots_quota(tmp_path: Path) -> None:
    engine, repository, objects, owners = _runtime(tmp_path, owner_count=1, capacity=20)
    service = JobService(repository, objects, JobServicePolicy(10))

    def submit(index: int) -> bool:
        try:
            service.submit(
                _request(owners[0].id, f"# source {index}".encode()),
                f"same-owner-{index}",
            )
        except JobUserQuotaExceededError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=12) as executor:
        admitted = tuple(executor.map(submit, range(20)))
    assert sum(admitted) == 1
    assert repository.list_owner(owners[0].id, offset=0, limit=20).total == 1
    engine.dispose()


@pytest.mark.integration
@pytest.mark.slow
def test_sqlite_short_submission_load_never_overshoots_capacity(tmp_path: Path) -> None:
    engine, repository, objects, owners = _runtime(tmp_path, owner_count=20, capacity=5)
    service = JobService(repository, objects, JobServicePolicy(10))

    def submit(owner: User) -> bool:
        try:
            service.submit(
                _request(owner.id, owner.normalized_username.encode()),
                owner.normalized_username,
            )
        except JobQueueCapacityExceededError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=12) as executor:
        admitted = tuple(executor.map(submit, owners))
    assert sum(admitted) == 5
    assert (
        sum(
            repository.list_owner(owner.id, offset=0, limit=2).total for owner in owners
        )
        == 5
    )
    engine.dispose()


@pytest.mark.integration
def test_retention_cleanup_expires_metadata_and_removes_private_source(
    tmp_path: Path,
) -> None:
    engine, repository, objects, owners = _runtime(tmp_path, owner_count=1, capacity=2)
    service = JobService(repository, objects, JobServicePolicy(10))
    queued, _ = service.submit(_request(owners[0].id, b"private"), "cleanup")
    source_key = ObjectKey(ObjectScope.UPLOAD, owners[0].id, queued.source_object_id)
    assert objects.exists(source_key)
    service.cancel(queued.id, actor_id=owners[0].id, actor_is_admin=False, now=NOW)

    class NoopProcessor:
        def process(self, *_args: object, **_kwargs: object) -> JobProcessResult:
            raise AssertionError("cleanup must not process jobs")

    worker = ConversionWorker(
        worker_id="cleanup",
        runtime=WorkerRuntime(
            repository,
            objects,
            NoopProcessor(),
            lambda: NOW + timedelta(seconds=11),
        ),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    assert worker.cleanup(limit=1) == 1
    expired = repository.get(queued.id)
    assert expired is not None and expired.state is JobState.EXPIRED
    assert not objects.exists(source_key)
    engine.dispose()


@pytest.mark.integration
def test_filesystem_result_cleanup_reclaims_lease_and_retries_deletion(
    tmp_path: Path,
) -> None:
    engine, repository, objects, owners = _runtime(tmp_path, owner_count=1, capacity=2)
    service = JobService(repository, objects, JobServicePolicy(10))
    queued, _ = service.submit(_request(owners[0].id, b"private"), "result-cleanup")

    class ResultProcessor:
        def process(self, *_args: object, **_kwargs: object) -> JobProcessResult:
            return JobProcessResult(b"result")

    processing_worker = ConversionWorker(
        worker_id="processor",
        runtime=WorkerRuntime(repository, objects, ResultProcessor(), lambda: NOW),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    assert processing_worker.run_once()
    succeeded = repository.get(queued.id)
    assert succeeded is not None and succeeded.result_object_id is not None
    source_key = ObjectKey(ObjectScope.UPLOAD, owners[0].id, queued.source_object_id)
    result_key = ObjectKey(ObjectScope.RESULT, owners[0].id, succeeded.result_object_id)
    failing_store = FailOnceResultDeleteStore(objects)
    failing_cleanup = ConversionWorker(
        worker_id="cleanup-failing",
        runtime=WorkerRuntime(
            repository,
            failing_store,
            ResultProcessor(),
            lambda: NOW + timedelta(seconds=11),
        ),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    with pytest.raises(ObjectStoreError):
        failing_cleanup.cleanup(limit=1)
    assert failing_store.failed
    assert not objects.exists(source_key)
    assert objects.exists(result_key)

    with Session(engine) as database:
        row = database.get(ConversionJobRow, str(queued.id))
        assert row is not None and row.cleanup_token is not None
        stale_token = UUID(row.cleanup_token)
    assert not repository.complete_cleanup(queued.id, uuid4())
    reclaimed = repository.expire_terminal(
        "cleanup-retry",
        NOW + timedelta(seconds=17),
        NOW + timedelta(seconds=22),
        1,
    )
    assert len(reclaimed) == 1 and reclaimed[0].cleanup_token != stale_token
    assert not repository.complete_cleanup(queued.id, stale_token)
    for object_id in reclaimed[0].result_object_ids:
        objects.delete(ObjectKey(ObjectScope.RESULT, owners[0].id, object_id))
    assert repository.complete_cleanup(queued.id, reclaimed[0].cleanup_token)
    assert not objects.exists(result_key)
    expired = repository.get(queued.id)
    assert expired is not None and expired.state is JobState.EXPIRED
    engine.dispose()
