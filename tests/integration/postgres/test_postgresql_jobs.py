"""Real PostgreSQL durable queue and concurrent claim coverage."""

import hashlib
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import boto3
import pytest
from sqlalchemy import delete, inspect

from md_converter.auth.models import Role, User
from md_converter.conversion.errors import ConversionErrorCode
from md_converter.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobRequest,
    JobState,
    JobStep,
)
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.jobs.worker import WorkerPolicy
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import downgrade_database, upgrade_database
from md_converter.persistence.schema import ConversionJobRow, TemplateRow, UserRow
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.storage import ObjectKey, ObjectScope, S3ObjectStore
from md_converter.templates.models import TemplateVersion
from md_converter.templates.processor import build_template_conversion_worker
from md_converter.templates.service import TemplateRecoveryPolicy, TemplateService
from md_converter.templates.validation import ValidatedTemplate
from tests.job_repository_contracts import (
    LEASE_END,
    NOW,
    RETENTION_END,
    TEMPLATE_ID,
    TEMPLATE_VERSION_ID,
    exercise_job_repository_contract,
    submission,
)
from tests.template_records import publish_template_pair

INTEGRITY_COLUMNS = {
    "source_filename",
    "source_kind",
    "source_sha256",
    "source_size",
    "result_manifest_object_id",
}


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_integrity_revision_round_trip_preserves_postgresql_schema() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    before = inspect(engine)
    expected_foreign_keys = before.get_foreign_keys("conversion_jobs")
    expected_indexes = before.get_indexes("conversion_jobs")
    try:
        try:
            downgrade_database(engine, "20260824_11")
            downgraded = inspect(engine)
            assert INTEGRITY_COLUMNS.isdisjoint(
                column["name"] for column in downgraded.get_columns("conversion_jobs")
            )
            assert (
                downgraded.get_foreign_keys("conversion_jobs") == expected_foreign_keys
            )
            assert downgraded.get_indexes("conversion_jobs") == expected_indexes
        finally:
            upgrade_database(engine)
        upgraded = inspect(engine)
        assert {
            column["name"] for column in upgraded.get_columns("conversion_jobs")
        } >= INTEGRITY_COLUMNS
        assert upgraded.get_foreign_keys("conversion_jobs") == expected_foreign_keys
        assert upgraded.get_indexes("conversion_jobs") == expected_indexes
    finally:
        engine.dispose()


class DistributedTemplateProcessor:
    """Return evidence of the exact frozen bytes supplied by composition."""

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
        assert template.id == job.template_version_id
        assert deadline_monotonic is None
        assert not cancelled()
        progress(JobStep.PUBLISHING, 90)
        return JobProcessResult(b"used:" + template_content)


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
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
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
                delete(TemplateRow).where(TemplateRow.id == str(TEMPLATE_ID))
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
    frozen_content = b"distributed-frozen-template"
    publish_template_pair(
        engine,
        owner.id,
        TEMPLATE_ID,
        TEMPLATE_VERSION_ID,
        sha256=hashlib.sha256(frozen_content).hexdigest(),
        size=len(frozen_content),
    )
    repository = SqlJobRepository(engine)
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ.get("MD_CONVERTER_TEST_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
    )
    objects = S3ObjectStore(client, os.environ["MD_CONVERTER_TEST_S3_BUCKET"])
    template_key = ObjectKey(
        ObjectScope.TEMPLATE_VERSION, owner.id, TEMPLATE_VERSION_ID
    )
    objects.put(template_key, frozen_content)
    resolver = TemplateService(
        catalog=SqlTemplateCatalogRepository(engine),
        selections=SqlTemplateSelectionRepository(engine),
        objects=objects,
        validate_content=lambda data, _declaration: ValidatedTemplate(
            hashlib.sha256(data).hexdigest(), (), (), (), ()
        ),
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    service = JobService(repository, objects, JobServicePolicy(60))
    queued, _ = service.submit(
        JobRequest(
            owner.id,
            b"# Distributed worker",
            TEMPLATE_ID,
            TEMPLATE_VERSION_ID,
            JobOutput.PDF,
            (("md-converter", "0.1.0"),),
            datetime.now(UTC),
        ),
        f"distributed-{unique}",
    )
    source_object_ids = [queued.source_object_id]
    worker = build_template_conversion_worker(
        worker_id=f"distributed-{unique}",
        repository=repository,
        objects=objects,
        resolver=resolver,
        processor=DistributedTemplateProcessor(),
        clock=lambda: datetime.now(UTC),
        policy=WorkerPolicy(1, 0.05, 60, 1),
    )
    try:
        assert worker.run_once()
        finished, content = service.download(
            queued.id, actor_id=owner.id, actor_is_admin=False
        )
        assert finished.state is JobState.SUCCEEDED
        assert content == b"used:" + frozen_content
        failed_job, _ = service.submit(
            JobRequest(
                owner.id,
                b"# Distributed failure",
                TEMPLATE_ID,
                TEMPLATE_VERSION_ID,
                JobOutput.PDF,
                (("md-converter", "0.1.0"),),
                datetime.now(UTC),
            ),
            f"distributed-failure-{unique}",
        )
        source_object_ids.append(failed_job.source_object_id)
        objects.put(template_key, b"tampered")
        failing_worker = build_template_conversion_worker(
            worker_id=f"distributed-failure-{unique}",
            repository=repository,
            objects=objects,
            resolver=resolver,
            processor=DistributedTemplateProcessor(),
            clock=lambda: datetime.now(UTC),
            policy=WorkerPolicy(1, 0.05, 60, 1),
        )
        assert failing_worker.run_once()
        failed = repository.get(failed_job.id)
        assert failed is not None and failed.state is JobState.FAILED
        assert failed.error_code == ConversionErrorCode.TEMPLATE_INTEGRITY.value
    finally:
        objects.delete(template_key)
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
            connection.execute(
                delete(TemplateRow).where(TemplateRow.id == str(TEMPLATE_ID))
            )
            connection.execute(delete(UserRow).where(UserRow.id == str(owner.id)))
        engine.dispose()
