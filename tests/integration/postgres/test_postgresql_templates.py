"""Real PostgreSQL template ownership and selection integration coverage."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import boto3
import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.auth.models import Role, User
from markweave.jobs.errors import JobRequestError
from markweave.jobs.models import JobOutput, JobSubmission
from markweave.persistence.errors import PersistenceError
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ConversionJobRow,
    SessionRow,
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import SqlUserRepository, create_database_engine
from markweave.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from markweave.storage import ObjectKey, ObjectScope, S3ObjectStore
from markweave.templates.errors import TemplateConflictError
from markweave.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)
from markweave.templates.service import TemplateRecoveryPolicy, TemplateService
from markweave.templates.validation import ValidatedTemplate
from tests.storage_contracts import (
    exercise_template_repository_contract,
    exercise_template_service_contract,
)


def clear_template_test_data(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(ConversionJobRow))
        connection.execute(delete(SystemTemplateSelectionRow))
        connection.execute(delete(TemplatePreferenceRow))
        connection.execute(delete(TemplateVersionRow))
        connection.execute(delete(TemplateRow))
        connection.execute(delete(SessionRow))
        connection.execute(delete(UserRow))


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_template_contract_constraints_and_immutability() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    clear_template_test_data(engine)
    users = SqlUserRepository(engine)
    catalog = SqlTemplateCatalogRepository(engine)
    selections = SqlTemplateSelectionRepository(engine)
    try:
        exercise_template_repository_contract(users, catalog, selections)
        exercise_template_service_contract(users, catalog, selections)

        owner = User(uuid4(), "Owner", f"owner-{uuid4()}", "hash:owner", Role.USER)
        other = User(uuid4(), "Other", f"other-{uuid4()}", "hash:other", Role.USER)
        users.create(owner)
        users.create(other)
        template = TemplateIdentity(
            uuid4(),
            owner.id,
            "Immutable PG",
            "Owner remains fixed",
            TemplateStatus.ACTIVE,
        )
        fallback = TemplateIdentity(
            uuid4(),
            other.id,
            "Fallback PG",
            "System fallback",
            TemplateStatus.ACTIVE,
        )
        catalog.add(template)
        catalog.add(fallback)
        with pytest.raises(PersistenceError):
            catalog.add(template)
        selections.set_system_fallback(fallback.id)
        selections.set_preferred(owner.id, template.id)
        with engine.begin() as connection:
            connection.execute(
                update(TemplateRow)
                .where(TemplateRow.id == str(template.id))
                .values(status=TemplateStatus.ARCHIVED.value)
            )
        assert selections.resolve(owner.id) == fallback

        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateRow)
                .where(TemplateRow.id == str(template.id))
                .values(owner_id=str(other.id))
            )
        persisted = catalog.get(template.id)
        assert persisted is not None
        assert persisted.owner_id == owner.id
        assert persisted.status is TemplateStatus.ARCHIVED
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateRow)
                .where(TemplateRow.id == str(template.id))
                .values(status="invalid")
            )
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(delete(UserRow).where(UserRow.id == str(owner.id)))
        with pytest.raises(PersistenceError):
            catalog.add(
                TemplateIdentity(
                    uuid4(),
                    uuid4(),
                    "Orphan PG",
                    "Missing owner",
                    TemplateStatus.ACTIVE,
                )
            )
        with pytest.raises(PersistenceError):
            selections.set_preferred(uuid4(), fallback.id)
    finally:
        clear_template_test_data(engine)
        engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_template_versions_and_concurrent_replacement(  # noqa: PLR0915
    request: pytest.FixtureRequest,
) -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    request.addfinalizer(engine.dispose)
    upgrade_database(engine)
    clear_template_test_data(engine)
    owner = User(uuid4(), "Owner", f"owner-{uuid4()}", "hash", Role.USER)
    SqlUserRepository(engine).create(owner)
    objects = S3ObjectStore(
        boto3.client(
            "s3",
            endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
            region_name=os.environ.get("MARKWEAVE_TEST_S3_REGION", "us-east-1"),
            aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
        ),
        os.environ["MARKWEAVE_TEST_S3_BUCKET"],
    )
    request.addfinalizer(objects.close)
    service = TemplateService(
        catalog=SqlTemplateCatalogRepository(engine),
        selections=SqlTemplateSelectionRepository(engine),
        objects=objects,
        validate_content=lambda data, _declaration: ValidatedTemplate(
            hashlib.sha256(data).hexdigest(),
            ("word/document.xml",),
            ("Calibri",),
            ("Calibri",),
            (("Calibri", "Carlito"),),
        ),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    try:
        template, first = service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "Distributed", "RustFS"),
            b"first",
            ("Calibri",),
        )
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateVersionRow)
                .where(TemplateVersionRow.id == str(first.id))
                .values(sha256="f" * 64)
            )
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateVersionRow)
                .where(TemplateVersionRow.id == str(first.id))
                .values(publication_state="pending")
            )
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateRow)
                .where(TemplateRow.id == str(template.id))
                .values(publication_state="pending")
            )

        def replace(content: bytes) -> object:
            try:
                return service.replace(
                    owner,
                    template.id,
                    expected_revision=1,
                    content=content,
                    expected_fonts=("Calibri",),
                )
            except TemplateConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(replace, (b"second-a", b"second-b")))
        assert sum(outcome is not None for outcome in outcomes) == 1
        versions = service.list_versions(owner, template.id)
        assert len(versions) == 2
        assert service.download(owner, template.id, first.id)[2] == b"first"
        _current_template, current_version, current = service.download(
            owner, template.id
        )
        assert current in {b"second-a", b"second-b"}
        listed = objects._client.list_objects_v2(
            Bucket=os.environ["MARKWEAVE_TEST_S3_BUCKET"],
            Prefix=f"template-versions/{owner.id}/",
        )
        assert listed.get("KeyCount") == 2

        other_template, other_version = service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "Other distributed", "Invariant target"),
            b"other",
            ("Calibri",),
        )
        now = datetime.now(UTC)

        def invalid_version(**overrides: object) -> TemplateVersionRow:
            values: dict[str, object] = {
                "id": str(uuid4()),
                "template_id": str(template.id),
                "version_number": 99,
                "object_owner_id": str(owner.id),
                "sha256": "e" * 64,
                "size": 1,
                "created_at": now,
                "created_by": str(owner.id),
                "restored_from_version_id": None,
                "declared_fonts": "[]",
                "resolved_fonts": "[]",
                "validation_trace": "[]",
                "publication_state": "published",
            }
            values.update(overrides)
            return TemplateVersionRow(**values)

        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.add(invalid_version(object_owner_id=str(uuid4())))
            database.flush()
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.add(
                invalid_version(
                    version_number=100,
                    restored_from_version_id=str(other_version.id),
                )
            )
            database.flush()
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateRow)
                .where(TemplateRow.id == str(template.id))
                .values(current_version_id=str(other_version.id))
            )

        def job_row(version_id: object) -> ConversionJobRow:
            return ConversionJobRow(
                id=str(uuid4()),
                owner_id=str(owner.id),
                source_object_id=str(uuid4()),
                template_id=str(template.id),
                template_version_id=str(version_id),
                output="docx",
                component_versions="[]",
                state="queued",
                step="queued",
                progress=0,
                request_digest="f" * 64,
                created_at=now,
                updated_at=now,
                attempt=0,
                source_ready=False,
                cancel_requested=False,
                cleanup_completed=False,
            )

        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.add(job_row(other_version.id))
            database.flush()
        with Session(engine) as database, database.begin():
            valid_job = job_row(current_version.id)
            valid_job_id = valid_job.id
            database.add(valid_job)
            database.flush()
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                delete(TemplateVersionRow).where(
                    TemplateVersionRow.id == str(current_version.id)
                )
            )
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(ConversionJobRow.id == valid_job_id)
            )

        pending_template = TemplateIdentity(
            uuid4(), owner.id, "Expired PG", "Publication lease", TemplateStatus.ACTIVE
        )
        pending_version = TemplateVersion(
            uuid4(),
            pending_template.id,
            1,
            owner.id,
            "a" * 64,
            1,
            now,
            owner.id,
            publication_state=TemplatePublicationState.PENDING,
            publication_token=uuid4(),
            publication_lease_expires_at=now - timedelta(seconds=1),
        )
        SqlTemplateCatalogRepository(engine).reserve_create(
            pending_template, pending_version
        )
        other_service = TemplateService(
            catalog=SqlTemplateCatalogRepository(engine),
            selections=SqlTemplateSelectionRepository(engine),
            objects=objects,
            validate_content=lambda data, _declaration: ValidatedTemplate(
                hashlib.sha256(data).hexdigest(), (), (), (), ()
            ),
            clock=lambda: now,
            recovery_policy=TemplateRecoveryPolicy(60),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            reclaimed = tuple(
                executor.map(
                    lambda candidate: candidate.reclaim_pending(),
                    (service, other_service),
                )
            )
        assert sorted(reclaimed) == [0, 1]

        jobs = SqlJobRepository(engine)

        def submission(template_id: UUID, version_id: UUID) -> JobSubmission:
            return JobSubmission(
                uuid4(),
                owner.id,
                uuid4(),
                template_id,
                version_id,
                JobOutput.DOCX,
                (("md-converter", "0.1.0"),),
                "a" * 64,
                None,
                now,
            )

        race_template, race_first = service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "PG submission replace", "Frozen pair"),
            b"first",
            ("Calibri",),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            submitted = executor.submit(
                jobs.create, submission(race_template.id, race_first.id)
            )
            replaced = executor.submit(
                service.replace,
                owner,
                race_template.id,
                expected_revision=1,
                content=b"second",
                expected_fonts=("Calibri",),
            )
        try:
            race_job, _ = submitted.result()
        except JobRequestError:
            race_job = None
        replaced.result()
        if race_job is not None:
            persisted = jobs.get(race_job.id)
            assert persisted is not None
            assert persisted.template_version_id == race_first.id
            with engine.begin() as connection:
                connection.execute(
                    delete(ConversionJobRow).where(
                        ConversionJobRow.id == str(race_job.id)
                    )
                )
        service.archive(owner, race_template.id, expected_revision=2)
        service.delete(owner, race_template.id, expected_revision=3)

        race_template, race_first = service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "PG submission archive", "Frozen pair"),
            b"first",
            ("Calibri",),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            submitted = executor.submit(
                jobs.create, submission(race_template.id, race_first.id)
            )
            archived = executor.submit(
                service.archive, owner, race_template.id, expected_revision=1
            )
        archived.result()
        try:
            race_job, _ = submitted.result()
        except JobRequestError:
            race_job = None
        if race_job is not None:
            persisted = jobs.get(race_job.id)
            assert persisted is not None
            assert persisted.template_version_id == race_first.id
            with engine.begin() as connection:
                connection.execute(
                    delete(ConversionJobRow).where(
                        ConversionJobRow.id == str(race_job.id)
                    )
                )
        service.delete(owner, race_template.id, expected_revision=2)

        race_template, race_first = service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "PG submission delete", "Archived pair"),
            b"first",
            ("Calibri",),
        )
        service.archive(owner, race_template.id, expected_revision=1)
        with ThreadPoolExecutor(max_workers=2) as executor:
            submitted = executor.submit(
                jobs.create, submission(race_template.id, race_first.id)
            )
            deleted = executor.submit(
                service.delete, owner, race_template.id, expected_revision=2
            )
        deleted.result()
        with pytest.raises(JobRequestError):
            submitted.result()

        service.archive(owner, template.id, expected_revision=2)
        service.delete(owner, template.id, expected_revision=3)
        assert all(
            not objects.exists(
                ObjectKey(
                    ObjectScope.TEMPLATE_VERSION,
                    version.object_owner_id,
                    version.id,
                )
            )
            for version in versions
        )
        service.archive(owner, other_template.id, expected_revision=1)
        service.delete(owner, other_template.id, expected_revision=2)
    finally:
        clear_template_test_data(engine)
