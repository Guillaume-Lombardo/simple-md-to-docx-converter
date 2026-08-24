"""Real SQLite durable queue integration coverage."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from md_converter.auth.models import Role, User
from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobRequest,
    JobState,
    JobStep,
)
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)
from tests.job_repository_contracts import exercise_job_repository_contract

COMPONENT_VERSIONS = (("md-converter", "0.1.0"),)


class DeterministicProcessor:
    """Small real worker boundary that deterministically publishes bytes."""

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert job.state is JobState.RUNNING
        assert not cancelled()
        progress(JobStep.DOCX, 70)
        return JobProcessResult(b"real-worker-result")


class FailingProcessor(DeterministicProcessor):
    """Expected safe processor failure used with real persistence boundaries."""

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert job.state is JobState.RUNNING
        raise ConversionError(
            ConversionErrorCode.INVALID_DOCX, "Conversion output is invalid."
        )


class ResultStoreFailure:
    """Delegate real source storage while failing result publication deterministically."""

    def __init__(self, delegate: ObjectStore) -> None:
        self._delegate = delegate

    def put(self, key: ObjectKey, content: bytes) -> None:
        if key.scope is ObjectScope.RESULT:
            raise ObjectStoreError("Object storage operation failed")
        self._delegate.put(key, content)

    def get(self, key: ObjectKey) -> bytes:
        return self._delegate.get(key)

    def delete(self, key: ObjectKey) -> None:
        self._delegate.delete(key)

    def exists(self, key: ObjectKey) -> bool:
        return self._delegate.exists(key)

    def is_ready(self) -> bool:
        return self._delegate.is_ready()


class BlockingResultStore(ResultStoreFailure):
    """Block a real result write while the worker must retain its lease."""

    def __init__(self, delegate: ObjectStore, entered: Event, release: Event) -> None:
        super().__init__(delegate)
        self._entered = entered
        self._release = release

    def put(self, key: ObjectKey, content: bytes) -> None:
        if key.scope is ObjectScope.RESULT:
            self._entered.set()
            if not self._release.wait(2):
                raise RuntimeError("Test result publication was not released")
            self._delegate.put(key, content)
            return
        self._delegate.put(key, content)


@pytest.mark.integration
def test_sqlite_job_repository_contract_and_restart(tmp_path: Path) -> None:
    database_url = standalone_database_url(tmp_path)
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    other = User(uuid4(), "Other", "other", "hash:other", Role.USER)
    users.create(owner)
    users.create(other)
    exercise_job_repository_contract(SqlJobRepository(engine), owner.id, other.id)
    engine.dispose()

    reopened = create_database_engine(database_url)
    assert (
        SqlJobRepository(reopened).list_owner(owner.id, offset=0, limit=100).total > 0
    )
    reopened.dispose()


@pytest.mark.integration
def test_sqlite_worker_crosses_real_database_and_filesystem_boundaries(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "worker-owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlJobRepository(engine)
    objects = FilesystemObjectStore(tmp_path)
    service = JobService(repository, objects, JobServicePolicy(60))
    queued, replayed = service.submit(
        JobRequest(
            owner.id,
            b"# Real worker",
            uuid4(),
            uuid4(),
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            datetime.now(UTC),
        ),
        "real-worker",
    )
    assert not replayed
    worker = ConversionWorker(
        worker_id="sqlite-worker",
        runtime=WorkerRuntime(
            repository, objects, DeterministicProcessor(), lambda: datetime.now(UTC)
        ),
        policy=WorkerPolicy(1, 0.05, 60, 1),
    )
    assert worker.run_once()
    finished, content = service.download(
        queued.id, actor_id=owner.id, actor_is_admin=False
    )
    assert finished.state is JobState.SUCCEEDED
    assert content == b"real-worker-result"
    engine.dispose()


@pytest.mark.integration
def test_periodic_heartbeat_prevents_long_sqlite_stage_recovery(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "heartbeat-owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlJobRepository(engine)
    objects = FilesystemObjectStore(tmp_path)
    service = JobService(repository, objects, JobServicePolicy(60))
    queued, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Slow stage",
            uuid4(),
            uuid4(),
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            datetime.now(UTC),
        ),
        None,
    )

    class SlowProcessor(DeterministicProcessor):
        def process(
            self,
            job: ConversionJob,
            *,
            cancelled: Callable[[], bool],
            progress: Callable[[JobStep, int], None],
        ) -> JobProcessResult:
            assert job.state is JobState.RUNNING
            assert not cancelled()
            time.sleep(0.16)
            return JobProcessResult(b"slow-result")

    worker = ConversionWorker(
        worker_id="heartbeat-worker",
        runtime=WorkerRuntime(
            repository, objects, SlowProcessor(), lambda: datetime.now(UTC)
        ),
        policy=WorkerPolicy(0.08, 0.02, 60, 1),
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker.run_once)
        time.sleep(0.11)
        now = datetime.now(UTC)
        assert repository.recover_expired_leases(now, now, now) == 0
        assert repository.claim("duplicate-worker", now, now) is None
        assert future.result()
    finished = repository.get(queued.id)
    assert finished is not None and finished.state is JobState.SUCCEEDED
    engine.dispose()


@pytest.mark.integration
def test_heartbeat_covers_blocked_real_result_publication(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "publish-owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlJobRepository(engine)
    files = FilesystemObjectStore(tmp_path)
    entered = Event()
    release = Event()
    objects = BlockingResultStore(files, entered, release)
    queued, _ = JobService(repository, objects, JobServicePolicy(60)).submit(
        JobRequest(
            owner.id,
            b"# Blocked publication",
            uuid4(),
            uuid4(),
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            datetime.now(UTC),
        ),
        None,
    )
    worker = ConversionWorker(
        worker_id="publication-worker",
        runtime=WorkerRuntime(
            repository, objects, DeterministicProcessor(), lambda: datetime.now(UTC)
        ),
        policy=WorkerPolicy(0.08, 0.02, 60, 1),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker.run_once)
            assert entered.wait(1)
            time.sleep(0.11)
            now = datetime.now(UTC)
            assert repository.recover_expired_leases(now, now, now) == 0
            assert repository.claim("duplicate-publication", now, now) is None
            release.set()
            assert future.result()
    finally:
        release.set()
    finished = repository.get(queued.id)
    assert finished is not None and finished.state is JobState.SUCCEEDED
    engine.dispose()


@pytest.mark.integration
def test_real_sqlite_worker_failures_are_durable_and_recoverable(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "failure-owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlJobRepository(engine)
    files = FilesystemObjectStore(tmp_path)
    service = JobService(repository, files, JobServicePolicy(60))
    now = datetime.now(UTC)
    failed_job, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Conversion failure",
            uuid4(),
            uuid4(),
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            now,
        ),
        "conversion-failure",
    )
    failing_worker = ConversionWorker(
        worker_id="conversion-failure-worker",
        runtime=WorkerRuntime(repository, files, FailingProcessor(), lambda: now),
        policy=WorkerPolicy(1, 0.05, 60, 1),
    )
    assert failing_worker.run_once()
    failed = repository.get(failed_job.id)
    assert failed is not None and failed.state is JobState.FAILED
    assert failed.error_code == ConversionErrorCode.INVALID_DOCX.value

    publication_job, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Publication failure",
            uuid4(),
            uuid4(),
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            now,
        ),
        "publication-failure",
    )
    publication_worker = ConversionWorker(
        worker_id="publication-failure-worker",
        runtime=WorkerRuntime(
            repository, ResultStoreFailure(files), DeterministicProcessor(), lambda: now
        ),
        policy=WorkerPolicy(1, 0.05, 60, 1),
    )
    with pytest.raises(ObjectStoreError):
        publication_worker.run_once()
    running = repository.get(publication_job.id)
    assert running is not None and running.state is JobState.RUNNING
    recovered_at = now + timedelta(seconds=2)
    assert repository.recover_expired_leases(recovered_at, recovered_at, now) == 1
    recovered = repository.get(publication_job.id)
    assert recovered is not None and recovered.state is JobState.QUEUED
    engine.dispose()


@pytest.mark.integration
def test_sqlite_job_service_idempotency_is_concurrent(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "idempotent-owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlJobRepository(engine)
    service = JobService(
        repository, FilesystemObjectStore(tmp_path), JobServicePolicy(60)
    )
    request = JobRequest(
        owner.id,
        b"# Concurrent idempotency",
        uuid4(),
        uuid4(),
        JobOutput.DOCX,
        COMPONENT_VERSIONS,
        datetime.now(UTC),
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(
            executor.map(
                lambda _index: service.submit(request, "same-owner-request"), range(8)
            )
        )
    assert len({job.id for job, _replayed in results}) == 1
    assert sum(not replayed for _job, replayed in results) == 1
    assert all(job.source_ready for job, _replayed in results)
    engine.dispose()
