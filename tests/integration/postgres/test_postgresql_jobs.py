"""Real PostgreSQL durable queue and concurrent claim coverage."""

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import boto3
import pytest
from sqlalchemy import delete

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
from md_converter.persistence.schema import ConversionJobRow, UserRow
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.storage import ObjectKey, ObjectScope, S3ObjectStore
from tests.job_repository_contracts import (
    LEASE_END,
    NOW,
    RETENTION_END,
    exercise_job_repository_contract,
    submission,
)


class DistributedProcessor:
    """Deterministic processor used to cross the real distributed boundaries."""

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert job.state is JobState.RUNNING
        assert not cancelled()
        progress(JobStep.PUBLISHING, 90)
        return JobProcessResult(b"distributed-worker-result")


class DistributedFailingProcessor(DistributedProcessor):
    """Safe processor failure crossing real PostgreSQL and S3 boundaries."""

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert job.state is JobState.RUNNING
        raise ConversionError(
            ConversionErrorCode.INVALID_PDF, "Conversion output is invalid."
        )


def race_cancel(
    barrier: Barrier,
    repository: SqlJobRepository,
    job_id: UUID,
    owner_id: UUID,
) -> None:
    barrier.wait()
    repository.request_cancel(
        job_id, owner_id, NOW + timedelta(seconds=2), RETENTION_END
    )


def race_recovery(barrier: Barrier, repository: SqlJobRepository) -> None:
    barrier.wait()
    repository.recover_expired_leases(
        NOW + timedelta(seconds=2), RETENTION_END, NOW - timedelta(days=1)
    )


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_job_contract_and_skip_locked_claims() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    unique = uuid4().hex
    owner = User(uuid4(), "Owner", f"owner-{unique}", "hash:owner", Role.USER)
    other = User(uuid4(), "Other", f"other-{unique}", "hash:other", Role.USER)
    users = SqlUserRepository(engine)
    users.create(owner)
    users.create(other)
    repository = SqlJobRepository(engine)
    try:
        exercise_job_repository_contract(repository, owner.id, other.id)
        while repository.claim(f"drain-{uuid4()}", NOW, LEASE_END) is not None:
            pass
        expected_ids = set()
        for index in range(3):
            queued, _ = repository.create(
                submission(owner.id, created_at=NOW + timedelta(minutes=index))
            )
            repository.activate_source(queued.id, NOW)
            expected_ids.add(queued.id)
        with ThreadPoolExecutor(max_workers=3) as executor:
            claimed = tuple(
                executor.map(
                    lambda worker: repository.claim(worker, NOW, LEASE_END),
                    ("concurrent-1", "concurrent-2", "concurrent-3"),
                )
            )
        claimed_ids = {job.id for job in claimed if job is not None}
        assert claimed_ids == expected_ids

        idempotent_digest = "a" * 64
        with ThreadPoolExecutor(max_workers=8) as executor:
            created = tuple(
                executor.map(
                    repository.create,
                    (
                        submission(owner.id, idempotency_digest=idempotent_digest)
                        for _index in range(8)
                    ),
                )
            )
        assert len({job.id for job, _replayed in created}) == 1
        assert sum(not replayed for _job, replayed in created) == 1

        for index in range(8):
            cancellation_job, _ = repository.create(
                submission(owner.id, created_at=NOW + timedelta(hours=index + 1))
            )
            repository.activate_source(cancellation_job.id, NOW)
            claimed_job = repository.claim(
                f"cancel-recovery-{index}", NOW, NOW + timedelta(seconds=1)
            )
            assert claimed_job is not None and claimed_job.id == cancellation_job.id
            barrier = Barrier(2)

            with ThreadPoolExecutor(max_workers=2) as executor:
                cancel_future = executor.submit(
                    race_cancel,
                    barrier,
                    repository,
                    cancellation_job.id,
                    owner.id,
                )
                recovery_future = executor.submit(race_recovery, barrier, repository)
                cancel_future.result()
                recovery_future.result()
            cancelled = repository.get(cancellation_job.id)
            assert cancelled is not None and cancelled.state is JobState.CANCELLED
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(
                    ConversionJobRow.owner_id.in_((str(owner.id), str(other.id)))
                )
            )
            connection.execute(
                delete(UserRow).where(UserRow.id.in_((str(owner.id), str(other.id))))
            )
        engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_worker_crosses_real_postgresql_and_s3_boundaries() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    unique = uuid4().hex
    owner = User(uuid4(), "Owner", f"worker-{unique}", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlJobRepository(engine)
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ.get("MD_CONVERTER_TEST_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
    )
    objects = S3ObjectStore(client, os.environ["MD_CONVERTER_TEST_S3_BUCKET"])
    service = JobService(repository, objects, JobServicePolicy(60))
    queued, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Distributed worker",
            uuid4(),
            uuid4(),
            JobOutput.PDF,
            (("md-converter", "0.1.0"),),
            datetime.now(UTC),
        ),
        f"distributed-{unique}",
    )
    source_object_ids = [queued.source_object_id]
    worker = ConversionWorker(
        worker_id=f"distributed-{unique}",
        runtime=WorkerRuntime(
            repository, objects, DistributedProcessor(), lambda: datetime.now(UTC)
        ),
        policy=WorkerPolicy(1, 0.05, 60, 1),
    )
    try:
        assert worker.run_once()
        finished, content = service.download(
            queued.id, actor_id=owner.id, actor_is_admin=False
        )
        assert finished.state is JobState.SUCCEEDED
        assert content == b"distributed-worker-result"
        failed_job, _ = service.submit(
            JobRequest(
                owner.id,
                b"# Distributed failure",
                uuid4(),
                uuid4(),
                JobOutput.PDF,
                (("md-converter", "0.1.0"),),
                datetime.now(UTC),
            ),
            f"distributed-failure-{unique}",
        )
        source_object_ids.append(failed_job.source_object_id)
        failing_worker = ConversionWorker(
            worker_id=f"distributed-failure-{unique}",
            runtime=WorkerRuntime(
                repository,
                objects,
                DistributedFailingProcessor(),
                lambda: datetime.now(UTC),
            ),
            policy=WorkerPolicy(1, 0.05, 60, 1),
        )
        assert failing_worker.run_once()
        failed = repository.get(failed_job.id)
        assert failed is not None and failed.state is JobState.FAILED
        assert failed.error_code == ConversionErrorCode.INVALID_PDF.value
    finally:
        current = repository.get(queued.id)
        for source_object_id in source_object_ids:
            objects.delete(ObjectKey(ObjectScope.UPLOAD, owner.id, source_object_id))
        if current is not None and current.result_object_id is not None:
            objects.delete(
                ObjectKey(ObjectScope.RESULT, owner.id, current.result_object_id)
            )
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(
                    ConversionJobRow.owner_id == str(owner.id)
                )
            )
            connection.execute(delete(UserRow).where(UserRow.id == str(owner.id)))
        engine.dispose()
