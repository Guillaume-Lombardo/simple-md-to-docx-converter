"""SQLite/filesystem admission, retention, and short-load coverage for T18."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from md_converter.auth.models import Role, User
from md_converter.jobs.errors import (
    JobQueueCapacityExceededError,
    JobUserQuotaExceededError,
)
from md_converter.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from md_converter.jobs.policy import JobAdmissionPolicy
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.storage import FilesystemObjectStore, ObjectKey, ObjectScope
from tests.job_repository_contracts import TEMPLATE_ID, TEMPLATE_VERSION_ID
from tests.template_records import publish_template_pair

NOW = datetime(2026, 8, 24, tzinfo=UTC)
COMPONENTS = (("md-converter", "0.1.0"),)


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
