"""Test-only setup and verification for final-image recovery CLI smoke coverage."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
from sqlalchemy import insert, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.engine import URL

from markweave.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import ConversionJobRow, UserRow
from markweave.persistence.sql import create_database_engine, standalone_database_url
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStore,
    S3ObjectStore,
)


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["RECOVERY_S3_ENDPOINT"],
        region_name="us-east-1",
        aws_access_key_id=os.environ["RECOVERY_S3_ACCESS"],
        aws_secret_access_key=os.environ["RECOVERY_S3_SECRET"],
    )


def _listed_object_count(response: dict[str, Any]) -> int:
    contents = response.get("Contents")
    if contents is None:
        return 0
    if not isinstance(contents, list):
        raise RuntimeError("S3 object listing is invalid")
    return len(contents)


def _upgrade_and_seed(database_url: str | URL) -> UUID:
    engine = create_database_engine(database_url)
    owner_id = uuid4()
    try:
        upgrade_database(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(UserRow),
                {
                    "id": str(owner_id),
                    "username": "Recovery E2E",
                    "normalized_username": f"recovery-e2e-{uuid4().hex}",
                    "password_hash": "hash:e2e",
                    "role": "user",
                    "active": True,
                    "auth_version": 0,
                    "password_change_required": False,
                },
            )
    finally:
        engine.dispose()
    return owner_id


def _seed_cleaned_job(
    database_url: str | URL, owner_id: UUID, objects: ObjectStore
) -> None:
    engine = create_database_engine(database_url)
    try:
        repository = SqlJobRepository(engine, JobAdmissionPolicy(2, 2))
        service = JobService(repository, objects, JobServicePolicy(10))
        now = datetime.now(UTC)
        job, replayed = service.submit(
            JobRequest(
                owner_id,
                b"# retention proof",
                None,
                None,
                JobOutput.DOCX,
                (("markweave", "e2e"),),
                now,
            ),
            "retention-proof",
        )
        if replayed:
            raise RuntimeError("retention proof replayed unexpectedly")
        source = ObjectKey(ObjectScope.UPLOAD, owner_id, job.source_object_id)
        service.cancel(job.id, actor_id=owner_id, actor_is_admin=False, now=now)

        class NoopProcessor:
            def process(self, *_args: object, **_kwargs: object) -> JobProcessResult:
                raise RuntimeError("retention proof must not process jobs")

        worker = ConversionWorker(
            worker_id="retention-proof",
            runtime=WorkerRuntime(
                repository,
                objects,
                NoopProcessor(),
                lambda: now + timedelta(seconds=11),
            ),
            policy=WorkerPolicy(5, 1, 10, 2),
        )
        if worker.cleanup(limit=1) != 1 or objects.exists(source):
            raise RuntimeError("retention proof did not remove its source")
        expired = repository.get(job.id)
        if expired is None or expired.state is not JobState.EXPIRED:
            raise RuntimeError("retention proof did not expire its job")
    finally:
        engine.dispose()


def _verify_cleaned_job(database_url: str | URL) -> None:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    ConversionJobRow.state,
                    ConversionJobRow.source_ready,
                    ConversionJobRow.cleanup_completed,
                )
            ).all()
            if rows != [("expired", True, True)]:
                raise RuntimeError("restored retention proof is invalid")
    finally:
        engine.dispose()


def _make_required_upload_missing(
    database_url: str | URL, objects: ObjectStore, *, incomplete: bool
) -> None:
    engine = create_database_engine(database_url)
    try:
        repository = SqlJobRepository(engine, JobAdmissionPolicy(2, 2))
        service = JobService(repository, objects, JobServicePolicy(10))
        now = datetime.now(UTC)
        if incomplete:
            with engine.connect() as connection:
                job_row = connection.execute(
                    select(
                        ConversionJobRow.id,
                        ConversionJobRow.owner_id,
                        ConversionJobRow.source_object_id,
                    ).where(ConversionJobRow.state == JobState.QUEUED.value)
                ).one()
            job_id = UUID(job_row.id)
            owner_id = UUID(job_row.owner_id)
            source = ObjectKey(
                ObjectScope.UPLOAD, owner_id, UUID(job_row.source_object_id)
            )
            if objects.exists(source):
                raise RuntimeError("missing-upload fixture unexpectedly has source")
            service.cancel(job_id, actor_id=owner_id, actor_is_admin=False, now=now)
            claimed = repository.expire_terminal(
                "incomplete-retention-proof",
                now + timedelta(seconds=11),
                now + timedelta(seconds=16),
                1,
            )
            if len(claimed) != 1 or claimed[0].job_id != job_id:
                raise RuntimeError("missing-upload fixture did not expire its job")
            expected_state = JobState.EXPIRED.value
        else:
            with engine.connect() as connection:
                owner = connection.scalar(select(UserRow.id))
            if owner is None:
                raise RuntimeError("missing-upload fixture has no owner")
            owner_id = UUID(owner)
            job, replayed = service.submit(
                JobRequest(
                    owner_id,
                    b"# required upload proof",
                    None,
                    None,
                    JobOutput.DOCX,
                    (("markweave", "e2e"),),
                    now,
                ),
                "required-upload-proof",
            )
            if replayed:
                raise RuntimeError("missing-upload fixture replayed unexpectedly")
            job_id = job.id
            source = ObjectKey(ObjectScope.UPLOAD, owner_id, job.source_object_id)
            if not objects.exists(source):
                raise RuntimeError("missing-upload fixture did not write source")
            objects.delete(source)
            expected_state = JobState.QUEUED.value
        with engine.connect() as connection:
            state, ready, completed = connection.execute(
                select(
                    ConversionJobRow.state,
                    ConversionJobRow.source_ready,
                    ConversionJobRow.cleanup_completed,
                ).where(ConversionJobRow.id == str(job_id))
            ).one()
        if (state, ready, completed) != (expected_state, True, False):
            raise RuntimeError("missing-upload fixture metadata is invalid")
        if objects.exists(source):
            raise RuntimeError("missing-upload fixture still has source")
    finally:
        engine.dispose()


def standalone_missing_upload(path: Path, *, incomplete: bool) -> None:
    _make_required_upload_missing(
        standalone_database_url(path),
        FilesystemObjectStore(path),
        incomplete=incomplete,
    )


def distributed_missing_upload(*, incomplete: bool) -> None:
    client = _s3()
    try:
        _make_required_upload_missing(
            os.environ["RECOVERY_DATABASE"],
            S3ObjectStore(client, os.environ["RECOVERY_SOURCE_BUCKET"]),
            incomplete=incomplete,
        )
    finally:
        client.close()


def verify_empty_target(path: Path | None = None) -> None:
    if path is not None:
        if path.exists() or path.is_symlink():
            raise RuntimeError("rejected standalone restore published a target")
        return
    engine = create_database_engine(os.environ["RECOVERY_DATABASE"])
    try:
        with engine.connect() as connection:
            if sqlalchemy_inspect(connection).get_table_names():
                raise RuntimeError("rejected distributed restore populated database")
    finally:
        engine.dispose()
    client = _s3()
    try:
        target = client.list_objects_v2(Bucket=os.environ["RECOVERY_MISSING_BUCKET"])
        if _listed_object_count(target) != 0:
            raise RuntimeError("rejected distributed restore populated bucket")
    finally:
        client.close()


def standalone_initialize(path: Path) -> None:
    path.mkdir(mode=0o700)
    object_path = path / "objects" / "uploads" / str(uuid4()) / str(uuid4())
    object_path.parent.mkdir(mode=0o700, parents=True)
    object_path.write_bytes(b"final-image-standalone")
    database_url = standalone_database_url(path)
    owner_id = _upgrade_and_seed(database_url)
    _seed_cleaned_job(database_url, owner_id, FilesystemObjectStore(path))


def standalone_verify(path: Path) -> None:
    engine = create_database_engine(standalone_database_url(path))
    try:
        with engine.connect() as connection:
            if connection.scalar(select(UserRow.username)) != "Recovery E2E":
                raise RuntimeError("standalone restored database is invalid")
    finally:
        engine.dispose()
    _verify_cleaned_job(standalone_database_url(path))
    objects = [
        item.read_bytes() for item in (path / "objects").rglob("*") if item.is_file()
    ]
    if objects != [b"final-image-standalone"]:
        raise RuntimeError("standalone restored objects are invalid")


def distributed_initialize() -> None:
    database_url = os.environ["RECOVERY_DATABASE"]
    owner_id = _upgrade_and_seed(database_url)
    client = _s3()
    for bucket in (
        os.environ["RECOVERY_SOURCE_BUCKET"],
        os.environ["RECOVERY_TARGET_BUCKET"],
        os.environ["RECOVERY_FAILED_BUCKET"],
        os.environ["RECOVERY_MISSING_BUCKET"],
    ):
        client.create_bucket(Bucket=bucket)
    client.put_object(
        Bucket=os.environ["RECOVERY_SOURCE_BUCKET"],
        Key=f"uploads/{uuid4()}/{uuid4()}",
        Body=b"final-image-distributed",
    )
    try:
        _seed_cleaned_job(
            database_url,
            owner_id,
            S3ObjectStore(client, os.environ["RECOVERY_SOURCE_BUCKET"]),
        )
    finally:
        client.close()


def distributed_verify() -> None:
    engine = create_database_engine(os.environ["RECOVERY_DATABASE"])
    try:
        with engine.connect() as connection:
            if connection.scalar(select(UserRow.username)) != "Recovery E2E":
                raise RuntimeError("distributed restored database is invalid")
    finally:
        engine.dispose()
    _verify_cleaned_job(os.environ["RECOVERY_DATABASE"])
    client = _s3()
    target = client.list_objects_v2(Bucket=os.environ["RECOVERY_TARGET_BUCKET"])
    if _listed_object_count(target) != 1:
        raise RuntimeError("distributed restore target is invalid")


def distributed_cleanup_verify() -> None:
    client = _s3()
    failed = client.list_objects_v2(Bucket=os.environ["RECOVERY_FAILED_BUCKET"])
    if _listed_object_count(failed) != 0:
        raise RuntimeError("distributed restore rollback cleanup is invalid")


def tamper(path: Path) -> None:
    database = path / "database" / "metadata.sqlite3"
    database.write_bytes(database.read_bytes() + b"tampered")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=(
            "standalone-initialize",
            "standalone-verify",
            "distributed-cleanup-verify",
            "distributed-initialize",
            "distributed-verify",
            "distributed-missing-active",
            "distributed-missing-incomplete",
            "standalone-missing-active",
            "standalone-missing-incomplete",
            "verify-empty-target",
            "tamper",
        ),
    )
    parser.add_argument("--path", required=False, type=Path)
    arguments = parser.parse_args()
    if arguments.operation == "standalone-initialize":
        standalone_initialize(arguments.path)
    elif arguments.operation == "standalone-verify":
        standalone_verify(arguments.path)
    elif arguments.operation == "distributed-initialize":
        distributed_initialize()
    elif arguments.operation == "distributed-cleanup-verify":
        distributed_cleanup_verify()
    elif arguments.operation == "distributed-verify":
        distributed_verify()
    elif arguments.operation == "distributed-missing-active":
        distributed_missing_upload(incomplete=False)
    elif arguments.operation == "distributed-missing-incomplete":
        distributed_missing_upload(incomplete=True)
    elif arguments.operation == "standalone-missing-active":
        standalone_missing_upload(arguments.path, incomplete=False)
    elif arguments.operation == "standalone-missing-incomplete":
        standalone_missing_upload(arguments.path, incomplete=True)
    elif arguments.operation == "verify-empty-target":
        verify_empty_target(arguments.path)
    else:
        tamper(arguments.path)


if __name__ == "__main__":
    main()
