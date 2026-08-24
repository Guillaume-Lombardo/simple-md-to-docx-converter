"""PostgreSQL admission serialization and short-load coverage for T18."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import delete

from md_converter.auth.models import Role, User
from md_converter.jobs.errors import JobQueueCapacityExceededError
from md_converter.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from md_converter.jobs.policy import JobAdmissionPolicy
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import ConversionJobRow, TemplateRow, UserRow
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    S3ObjectStore,
)
from tests.template_records import publish_template_pair

NOW = datetime(2026, 8, 24, tzinfo=UTC)


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
            assert sum(executor.map(submit, users)) == 3
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
def test_distributed_retention_cleanup_removes_rustfs_source() -> None:
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
    service.cancel(queued.id, actor_id=owner.id, actor_is_admin=False, now=NOW)

    class NoopProcessor:
        def process(self, *_args: object, **_kwargs: object) -> JobProcessResult:
            raise AssertionError("cleanup must not process jobs")

    worker = ConversionWorker(
        worker_id=f"cleanup-{suffix}",
        runtime=WorkerRuntime(
            repository,
            objects,
            NoopProcessor(),
            lambda: NOW + timedelta(seconds=11),
        ),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    try:
        assert objects.exists(source_key)
        assert worker.cleanup(limit=1) == 1
        expired = repository.get(queued.id)
        assert expired is not None and expired.state is JobState.EXPIRED
        assert not objects.exists(source_key)
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
