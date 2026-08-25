"""Real SQLite durable queue integration coverage."""

import hashlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect

from markweave.auth.models import Role, User
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobRequest,
    JobState,
    JobStep,
    LeaseHeartbeat,
)
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from markweave.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)
from markweave.templates.models import TemplateVersion
from markweave.templates.processor import build_template_conversion_worker
from markweave.templates.service import TemplateRecoveryPolicy, TemplateService
from markweave.templates.validation import ValidatedTemplate
from tests.job_repository_contracts import (
    TEMPLATE_ID,
    TEMPLATE_VERSION_ID,
    exercise_job_repository_contract,
)
from tests.sqlite_compatibility import (
    enforce_sqlite_334_alter_grammar,
    enforce_sqlite_334_update_grammar,
)
from tests.template_records import publish_template_pair

COMPONENT_VERSIONS = (("md-converter", "0.1.0"),)
INTEGRITY_COLUMNS = {
    "source_filename",
    "source_kind",
    "source_sha256",
    "source_size",
    "result_manifest_object_id",
}


@pytest.mark.integration
def test_integrity_revision_round_trip_uses_sqlite_334_table_copy(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    enforce_sqlite_334_alter_grammar(engine)
    before = inspect(engine)
    expected_foreign_keys = before.get_foreign_keys("conversion_jobs")
    expected_indexes = before.get_indexes("conversion_jobs")
    assert {
        column["name"] for column in before.get_columns("conversion_jobs")
    } >= INTEGRITY_COLUMNS

    downgrade_database(engine, "20260824_11")
    downgraded = inspect(engine)
    assert INTEGRITY_COLUMNS.isdisjoint(
        column["name"] for column in downgraded.get_columns("conversion_jobs")
    )
    assert downgraded.get_foreign_keys("conversion_jobs") == expected_foreign_keys
    assert downgraded.get_indexes("conversion_jobs") == expected_indexes

    upgrade_database(engine)
    upgraded = inspect(engine)
    assert {
        column["name"] for column in upgraded.get_columns("conversion_jobs")
    } >= INTEGRITY_COLUMNS
    assert upgraded.get_foreign_keys("conversion_jobs") == expected_foreign_keys
    assert upgraded.get_indexes("conversion_jobs") == expected_indexes
    engine.dispose()


class ControlledClock:
    """Thread-safe logical clock for deterministic lease integration tests."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._lock = Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> datetime:
        with self._lock:
            self._now += timedelta(seconds=seconds)
            return self._now


class ObservedHeartbeatRepository(SqlJobRepository):
    """Signal only after a real heartbeat uses the requested logical time."""

    def __init__(
        self, engine: Engine, observed: Event, minimum_observed_at: datetime
    ) -> None:
        super().__init__(engine)
        self._observed = observed
        self._minimum_observed_at = minimum_observed_at

    def heartbeat(self, heartbeat: LeaseHeartbeat) -> bool:
        renewed = super().heartbeat(heartbeat)
        if renewed and heartbeat.now >= self._minimum_observed_at:
            self._observed.set()
        return renewed


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


class TemplateBytesProcessor:
    """Prove the composed worker receives the persisted frozen bytes."""

    def process_with_template(  # noqa: PLR0913 - explicit worker boundary
        self,
        job: ConversionJob,
        template: TemplateVersion,
        template_content: bytes,
        *,
        cancelled: Callable[[], bool],
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert not cancelled()
        assert deadline_monotonic is None
        assert template.id == job.template_version_id
        progress(JobStep.DOCX, 70)
        return JobProcessResult(b"used:" + template_content)


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
    enforce_sqlite_334_update_grammar(engine)
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    other = User(uuid4(), "Other", "other", "hash:other", Role.USER)
    users.create(owner)
    users.create(other)
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
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
    frozen_content = b"exact-frozen-template"
    publish_template_pair(
        engine,
        owner.id,
        TEMPLATE_ID,
        TEMPLATE_VERSION_ID,
        sha256=hashlib.sha256(frozen_content).hexdigest(),
        size=len(frozen_content),
    )
    repository = SqlJobRepository(engine)
    objects = FilesystemObjectStore(tmp_path)
    objects.put(
        ObjectKey(ObjectScope.TEMPLATE_VERSION, owner.id, TEMPLATE_VERSION_ID),
        frozen_content,
    )
    service = JobService(repository, objects, JobServicePolicy(60))
    queued, replayed = service.submit(
        JobRequest(
            owner.id,
            b"# Real worker",
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            datetime.now(UTC),
        ),
        "real-worker",
    )
    assert not replayed
    resolver = TemplateService(
        catalog=SqlTemplateCatalogRepository(engine),
        selections=SqlTemplateSelectionRepository(engine),
        objects=objects,
        validate_content=lambda data, _declaration: ValidatedTemplate(
            hashlib.sha256(data).hexdigest(), (), (), (), ()
        ),
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    worker = build_template_conversion_worker(
        worker_id="sqlite-worker",
        repository=repository,
        objects=objects,
        resolver=resolver,
        processor=TemplateBytesProcessor(),
        clock=lambda: datetime.now(UTC),
        policy=WorkerPolicy(1, 0.05, 60, 1),
    )
    assert worker.run_once()
    finished, content = service.download(
        queued.id, actor_id=owner.id, actor_is_admin=False
    )
    assert finished.state is JobState.SUCCEEDED
    assert content == b"used:" + frozen_content

    failed_job, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Integrity failure",
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
            JobOutput.DOCX,
            COMPONENT_VERSIONS,
            datetime.now(UTC),
        ),
        "template-integrity",
    )
    objects.put(
        ObjectKey(ObjectScope.TEMPLATE_VERSION, owner.id, TEMPLATE_VERSION_ID),
        b"tampered",
    )
    assert worker.run_once()
    failed = repository.get(failed_job.id)
    assert failed is not None
    assert failed.state is JobState.FAILED
    assert failed.error_code == ConversionErrorCode.TEMPLATE_INTEGRITY.value
    engine.dispose()


@pytest.mark.integration
def test_periodic_heartbeat_prevents_long_sqlite_stage_recovery(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "heartbeat-owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
    initial_time = datetime(2026, 8, 24, tzinfo=UTC)
    clock = ControlledClock(initial_time)
    heartbeat_observed = Event()
    repository = ObservedHeartbeatRepository(
        engine,
        heartbeat_observed,
        initial_time + timedelta(seconds=0.06),
    )
    objects = FilesystemObjectStore(tmp_path)
    service = JobService(repository, objects, JobServicePolicy(60))
    queued, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Slow stage",
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
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
            processor_entered.set()
            assert processor_release.wait(2)
            return JobProcessResult(b"slow-result")

    processor_entered = Event()
    processor_release = Event()
    worker = ConversionWorker(
        worker_id="heartbeat-worker",
        runtime=WorkerRuntime(repository, objects, SlowProcessor(), clock),
        policy=WorkerPolicy(0.08, 0.02, 60, 1),
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker.run_once)
        try:
            assert processor_entered.wait(1)
            clock.advance(0.06)
            assert heartbeat_observed.wait(1)
            now = clock.advance(0.04)
            assert now > initial_time + timedelta(seconds=0.08)
            assert repository.recover_expired_leases(now, now, now) == 0
            assert repository.claim("duplicate-worker", now, now) is None
        finally:
            processor_release.set()
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
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
    repository = SqlJobRepository(engine)
    files = FilesystemObjectStore(tmp_path)
    entered = Event()
    release = Event()
    objects = BlockingResultStore(files, entered, release)
    queued, _ = JobService(repository, objects, JobServicePolicy(60)).submit(
        JobRequest(
            owner.id,
            b"# Blocked publication",
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
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
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
    repository = SqlJobRepository(engine)
    files = FilesystemObjectStore(tmp_path)
    service = JobService(repository, files, JobServicePolicy(60))
    now = datetime.now(UTC)
    failed_job, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Conversion failure",
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
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
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
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
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
    repository = SqlJobRepository(engine)
    service = JobService(
        repository, FilesystemObjectStore(tmp_path), JobServicePolicy(60)
    )
    request = JobRequest(
        owner.id,
        b"# Concurrent idempotency",
        TEMPLATE_ID,
        TEMPLATE_VERSION_ID,
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
