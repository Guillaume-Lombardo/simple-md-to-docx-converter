"""Test-only setup and verification for final-image recovery CLI smoke coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
from sqlalchemy import Connection, Engine, MetaData, Table, insert, select, text
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.engine import URL

from markweave.config import StorageProfile
from markweave.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database, upgrade_database_to
from markweave.persistence.schema import ConversionJobRow, UserRow
from markweave.persistence.sql import create_database_engine, standalone_database_url
from markweave.recovery_adapters import DATABASE_PAYLOAD, _encode
from markweave.recovery_manifest import (
    RecoveryIdentity,
    RecoveryMember,
    build_manifest,
    canonical_json,
    sha256_file,
    write_manifest,
)
from markweave.recovery_schema import AUXILIARY_TABLES, head_revision
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
        if any(path.parent.glob(f".{path.name}.restore-*")):
            raise RuntimeError("rejected standalone restore left a staging path")
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


# The last schema before T91 added `composer_fill_plans` in migration 20260924_25.
PRE_FILL_PLAN_REVISION = "20260923_24"


@dataclass(frozen=True, slots=True)
class OlderSchemaRecords:
    """Identifiers and stable objects referenced by the inserted rows."""

    owner_id: str
    job_id: str
    draft_id: str
    revision_id: str
    template_id: str
    template_version_id: str
    objects: dict[str, bytes]

    def key(self, scope: str) -> str:
        return next(key for key in self.objects if key.startswith(f"{scope}/"))


def _table(connection: Connection, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=connection)


def populate_older_schema(engine: Engine) -> OlderSchemaRecords:
    """Insert one user with a template, a conversion job, and a Composer revision."""

    owner = str(uuid4())
    template_id, version_id = str(uuid4()), str(uuid4())
    job_id, upload_id, result_id = str(uuid4()), str(uuid4()), str(uuid4())
    source_id, draft_id = str(uuid4()), str(uuid4())
    revision_id, artifact_id = str(uuid4()), str(uuid4())
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    source_reference = f'{{"object_id": "{source_id}"}}'
    with engine.begin() as connection:
        connection.execute(
            insert(_table(connection, "users")),
            {
                "id": owner,
                "username": "Older schema owner",
                "normalized_username": f"older-{uuid4().hex}",
                "password_hash": "hash:older",
                "role": "user",
                "active": True,
                "auth_version": 0,
                "password_change_required": False,
            },
        )
        connection.execute(
            insert(_table(connection, "templates")),
            {
                "id": template_id,
                "owner_id": owner,
                "name": "Older template",
                "normalized_name": "older template",
                "description": "",
                "normalized_description": "",
                "status": "active",
                "revision": 1,
                "publication_state": "published",
                "kind": "docx",
            },
        )
        connection.execute(
            insert(_table(connection, "template_versions")),
            {
                "id": version_id,
                "template_id": template_id,
                "version_number": 1,
                "object_owner_id": owner,
                "sha256": "a" * 64,
                "size": 8,
                "created_at": now,
                "created_by": owner,
                "declared_fonts": "[]",
                "resolved_fonts": "[]",
                "validation_trace": "{}",
                "publication_state": "published",
            },
        )
        # Select the published version as current, as template publication does.
        templates = _table(connection, "templates")
        connection.execute(
            templates.update()
            .where(templates.c.id == template_id)
            .values(current_version_id=version_id)
        )
        connection.execute(
            insert(_table(connection, "conversion_jobs")),
            {
                "id": job_id,
                "owner_id": owner,
                "source_object_id": upload_id,
                "output": "docx",
                "component_versions": "{}",
                "state": "succeeded",
                "step": "complete",
                "progress": 100,
                "request_digest": "b" * 64,
                "created_at": now,
                "updated_at": now,
                "attempt": 1,
                "source_ready": True,
                "cancel_requested": False,
                "cleanup_completed": False,
                "result_object_id": result_id,
                "template_id": template_id,
                "template_version_id": version_id,
            },
        )
        connection.execute(
            insert(_table(connection, "composer_sources")),
            {
                "id": source_id,
                "owner_id": owner,
                "sha256": "c" * 64,
                "size": 6,
                "scan_receipt": "scan-ok",
                "media_type": "text/markdown",
                "publication_state": "published",
                "created_at": now,
                "kind": "upload",
            },
        )
        connection.execute(
            insert(_table(connection, "composer_drafts")),
            {
                "id": draft_id,
                "owner_id": owner,
                "title": "Older draft",
                "source_reference": source_reference,
                "content": "# Older draft",
                "state": "active",
                "version": 1,
                "current_revision_id": revision_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            insert(_table(connection, "composer_revisions")),
            {
                "id": revision_id,
                "draft_id": draft_id,
                "number": 1,
                "actor_id": owner,
                "expected_draft_version": 1,
                "source_reference": source_reference,
                "approved_values": "{}",
                "render_options": "{}",
                "provenance": "human",
                "operation": "generate",
                "idempotency_key": "older-schema",
                "request_digest": "d" * 64,
                "publication_state": "published",
                "created_at": now,
            },
        )
        connection.execute(
            insert(_table(connection, "composer_artifacts")),
            {
                "id": artifact_id,
                "revision_id": revision_id,
                "kind": "download",
                "sha256": "e" * 64,
                "size": 8,
                "media_type": "text/plain",
            },
        )
    return OlderSchemaRecords(
        owner,
        job_id,
        draft_id,
        revision_id,
        template_id,
        version_id,
        {
            f"template-versions/{owner}/{version_id}": b"template",
            f"uploads/{owner}/{upload_id}": b"# source",
            f"results/{owner}/{result_id}": b"result",
            f"composer-sources/{owner}/{source_id}": b"source",
            f"composer-artifacts/{owner}/{artifact_id}": b"download",
        },
    )


def _older_records_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.records.json")


def _write_older_records(path: Path, records: OlderSchemaRecords) -> None:
    path.write_text(
        json.dumps(
            asdict(records)
            | {
                "objects": {
                    key: content.decode("ascii")
                    for key, content in records.objects.items()
                }
            }
        ),
        encoding="utf-8",
    )


def _read_older_records(path: Path) -> OlderSchemaRecords:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["objects"] = {
        key: content.encode("ascii") for key, content in raw["objects"].items()
    }
    return OlderSchemaRecords(**raw)


def _verify_older_database(
    database_url: str | URL, records: OlderSchemaRecords
) -> None:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            if revision != head_revision():
                raise RuntimeError("restored earlier-schema set did not reach head")
            if (
                "composer_fill_plans"
                not in sqlalchemy_inspect(connection).get_table_names()
            ):
                raise RuntimeError("restored earlier-schema set lacks later tables")
            job = connection.execute(
                text("SELECT state, owner_id FROM conversion_jobs WHERE id = :id"),
                {"id": records.job_id},
            ).one()
            if tuple(job) != ("succeeded", records.owner_id):
                raise RuntimeError("restored earlier-schema job is invalid")
            revision_state = connection.scalar(
                text("SELECT publication_state FROM composer_revisions WHERE id = :id"),
                {"id": records.revision_id},
            )
            if revision_state != "published":
                raise RuntimeError("restored earlier-schema revision is invalid")
            pair = connection.execute(
                text(
                    "SELECT j.template_id, j.template_version_id, t.current_version_id "
                    "FROM conversion_jobs j JOIN templates t ON t.id = j.template_id "
                    "WHERE j.id = :id"
                ),
                {"id": records.job_id},
            ).one()
            if tuple(pair) != (
                records.template_id,
                records.template_version_id,
                records.template_version_id,
            ):
                raise RuntimeError("restored earlier-schema template pair is invalid")
    finally:
        engine.dispose()


def standalone_older_initialize(path: Path) -> None:
    (path / "objects").mkdir(mode=0o700, parents=True)
    engine = create_database_engine(standalone_database_url(path))
    try:
        upgrade_database_to(engine, PRE_FILL_PLAN_REVISION)
        records = populate_older_schema(engine)
    finally:
        engine.dispose()
    for key, content in records.objects.items():
        object_path = path / "objects" / key
        object_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        object_path.write_bytes(content)
    _write_older_records(_older_records_path(path), records)


def standalone_older_alter(path: Path, statement: str) -> None:
    with closing(sqlite3.connect(path / "metadata.sqlite3")) as connection:
        connection.execute(statement)
        connection.commit()


def standalone_older_verify(path: Path, records_path: Path) -> None:
    records = _read_older_records(records_path)
    _verify_older_database(standalone_database_url(path), records)
    for key, content in records.objects.items():
        if (path / "objects" / key).read_bytes() != content:
            raise RuntimeError("restored earlier-schema object is invalid")


def distributed_older_initialize(records_path: Path) -> None:
    database_url = os.environ["RECOVERY_DATABASE"]
    engine = create_database_engine(database_url)
    try:
        upgrade_database_to(engine, PRE_FILL_PLAN_REVISION)
        records = populate_older_schema(engine)
    finally:
        engine.dispose()
    client = _s3()
    try:
        for bucket in filter(None, os.environ["RECOVERY_CREATE_BUCKETS"].split(",")):
            client.create_bucket(Bucket=bucket)
        for key, content in records.objects.items():
            client.put_object(
                Bucket=os.environ["RECOVERY_SOURCE_BUCKET"], Key=key, Body=content
            )
    finally:
        client.close()
    _write_older_records(records_path, records)


def distributed_empty_initialize(revision: str) -> None:
    """Migrate an empty database to `revision` and seed one stable object."""

    engine = create_database_engine(os.environ["RECOVERY_DATABASE"])
    try:
        upgrade_database_to(engine, revision)
    finally:
        engine.dispose()
    client = _s3()
    try:
        for bucket in filter(None, os.environ["RECOVERY_CREATE_BUCKETS"].split(",")):
            client.create_bucket(Bucket=bucket)
        client.put_object(
            Bucket=os.environ["RECOVERY_SOURCE_BUCKET"],
            Key=f"uploads/{uuid4()}/{uuid4()}",
            Body=b"final-image-historical",
        )
    finally:
        client.close()


def distributed_historical_set(
    destination: Path, schema_revision: str | None, omit_tables: frozenset[str]
) -> None:
    client = _s3()
    try:
        backup_id = write_historical_distributed_set(
            destination,
            os.environ["RECOVERY_DATABASE"],
            client,
            (
                os.environ["RECOVERY_S3_ENDPOINT"],
                "us-east-1",
                os.environ["RECOVERY_SOURCE_BUCKET"],
            ),
            schema_revision=schema_revision,
            omit_tables=omit_tables,
        )
    finally:
        client.close()
    print(backup_id)


def distributed_older_alter(statement: str) -> None:
    engine = create_database_engine(os.environ["RECOVERY_DATABASE"])
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def distributed_older_verify(records_path: Path) -> None:
    records = _read_older_records(records_path)
    _verify_older_database(os.environ["RECOVERY_DATABASE"], records)
    client = _s3()
    try:
        bucket = os.environ["RECOVERY_TARGET_BUCKET"]
        for key, content in records.objects.items():
            response = client.get_object(Bucket=bucket, Key=key)
            with closing(response["Body"]) as body:
                if body.read() != content:
                    raise RuntimeError("restored earlier-schema object is invalid")
    finally:
        client.close()


def _migration_shape(revision: str) -> dict[str, list[str]]:
    """Return the migration-defined model tables and columns at one revision."""

    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    try:
        upgrade_database_to(engine, revision)
        with engine.connect() as connection:
            inspector = sqlalchemy_inspect(connection)
            return {
                name: [column["name"] for column in inspector.get_columns(name)]
                for name in inspector.get_table_names()
                if name not in AUXILIARY_TABLES
            }
    finally:
        engine.dispose()


def historical_database_payload(
    connection: Connection,
    *,
    schema_revision: str | None = None,
    omit_tables: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Build a logical payload the way an earlier release's backup wrote it.

    Test-only: production backup always captures the current model. Only tables
    and columns that the migrations define at `schema_revision` are read; the
    payload records the database's own Alembic revision.
    """

    revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    shape = _migration_shape(schema_revision or revision)
    order = [
        name
        for name, _constraints in sqlalchemy_inspect(
            connection
        ).get_sorted_table_and_fkc_names()
        if name in shape and name not in omit_tables
    ]
    if set(order) | omit_tables != set(shape):
        raise RuntimeError("historical payload source lacks a migration table")
    tables = []
    for name in order:
        table = Table(name, MetaData(), autoload_with=connection)
        columns = shape[name]
        rows = connection.execute(select(*(table.c[column] for column in columns)))
        tables.append(
            {
                "name": name,
                "columns": columns,
                "rows": [
                    {key: _encode(value) for key, value in row.items()}
                    for row in rows.mappings()
                ],
            }
        )
    return {
        "schema": "markweave-postgresql-logical-v1",
        "revision": revision,
        "tables": tables,
    }


def write_historical_distributed_set(  # noqa: PLR0913 - explicit provider identity
    destination: Path,
    database_url: str | URL,
    client: Any,
    configuration: tuple[str, str, str],
    *,
    schema_revision: str | None = None,
    omit_tables: frozenset[str] = frozenset(),
) -> str:
    """Publish a checksummed distributed set for a historical schema revision."""

    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = destination / f".historical-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    payload_path = staging / DATABASE_PAYLOAD
    payload_path.parent.mkdir(mode=0o700)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            payload = historical_database_payload(
                connection, schema_revision=schema_revision, omit_tables=omit_tables
            )
            resource = connection.execute(
                text(
                    "SELECT current_database(), current_schema(), "
                    "COALESCE(inet_server_addr()::text, 'local'), inet_server_port()"
                )
            ).one()
    finally:
        engine.dispose()
    payload_path.write_bytes(canonical_json(payload) + b"\n")
    size, digest = sha256_file(payload_path)
    members = [RecoveryMember(DATABASE_PAYLOAD, size, digest)]
    endpoint, region, bucket = configuration
    listed = client.list_objects_v2(Bucket=bucket)
    inventory = sorted(item["Key"] for item in listed.get("Contents", []))
    for key in inventory:
        target = staging / "objects" / key
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        response = client.get_object(Bucket=bucket, Key=key)
        with closing(response["Body"]) as body:
            target.write_bytes(body.read())
        object_size, object_digest = sha256_file(target)
        members.append(RecoveryMember(f"objects/{key}", object_size, object_digest))
    database_source = hashlib.sha256(canonical_json(list(resource))).hexdigest()
    # The same provider identity the S3 recovery adapter derives for its source.
    object_source = hashlib.sha256(
        canonical_json([endpoint, region, bucket])
    ).hexdigest()
    manifest = build_manifest(
        StorageProfile.DISTRIBUTED,
        created_at=datetime.now(UTC),
        identity=RecoveryIdentity(
            hashlib.sha256(digest.encode()).hexdigest(),
            hashlib.sha256(canonical_json(inventory)).hexdigest(),
            "historical-test-producer",
            f"{database_source}.{object_source}",
        ),
        members=tuple(members),
    )
    write_manifest(staging, manifest)
    os.replace(staging, destination / manifest.backup_id)
    return manifest.backup_id


# Fixed test-only schema changes that a supported restore must refuse.
_OLDER_ALTERATIONS = {
    "drop-table": "DROP TABLE composer_questions",
    "newer-revision": "UPDATE alembic_version SET version_num = '20991231_99'",
}


def tamper(path: Path) -> None:
    database = path / "database" / "metadata.sqlite3"
    database.write_bytes(database.read_bytes() + b"tampered")


def _operations(arguments: argparse.Namespace) -> dict[str, Callable[[], None]]:
    path, records = arguments.path, arguments.records
    statement = _OLDER_ALTERATIONS.get(arguments.alteration or "", "")
    return {
        "standalone-initialize": lambda: standalone_initialize(path),
        "standalone-verify": lambda: standalone_verify(path),
        "distributed-cleanup-verify": distributed_cleanup_verify,
        "distributed-initialize": distributed_initialize,
        "distributed-verify": distributed_verify,
        "distributed-missing-active": lambda: distributed_missing_upload(
            incomplete=False
        ),
        "distributed-missing-incomplete": lambda: distributed_missing_upload(
            incomplete=True
        ),
        "standalone-missing-active": lambda: standalone_missing_upload(
            path, incomplete=False
        ),
        "standalone-missing-incomplete": lambda: standalone_missing_upload(
            path, incomplete=True
        ),
        "verify-empty-target": lambda: verify_empty_target(path),
        "tamper": lambda: tamper(path),
        "standalone-older-initialize": lambda: standalone_older_initialize(path),
        "standalone-older-alter": lambda: standalone_older_alter(path, statement),
        "standalone-older-verify": lambda: standalone_older_verify(path, records),
        "distributed-older-initialize": lambda: distributed_older_initialize(records),
        "distributed-older-alter": lambda: distributed_older_alter(statement),
        "distributed-older-verify": lambda: distributed_older_verify(records),
        "distributed-empty-initialize": lambda: distributed_empty_initialize(
            arguments.revision
        ),
        "distributed-historical-set": lambda: distributed_historical_set(
            path, arguments.schema_revision, frozenset(arguments.omit_table)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation")
    parser.add_argument("--path", required=False, type=Path)
    parser.add_argument("--records", required=False, type=Path)
    parser.add_argument("--alteration", choices=tuple(_OLDER_ALTERATIONS))
    parser.add_argument("--revision")
    parser.add_argument("--schema-revision")
    parser.add_argument("--omit-table", action="append", default=[])
    arguments = parser.parse_args()
    operations = _operations(arguments)
    if arguments.operation not in operations:
        parser.error(f"unknown operation: {arguments.operation}")
    operations[arguments.operation]()


if __name__ == "__main__":
    main()
