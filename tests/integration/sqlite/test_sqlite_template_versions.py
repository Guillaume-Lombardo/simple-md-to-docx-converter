"""Real SQLite/filesystem coverage for versioned template transactions."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.auth.models import Role, User
from markweave.jobs.errors import JobRequestError
from markweave.jobs.models import JobOutput, JobSubmission
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ConversionJobRow,
    TemplateAuditRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope
from markweave.templates.errors import (
    TemplateConflictError,
    TemplateIntegrityError,
    TemplateUnavailableError,
)
from markweave.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)
from markweave.templates.service import TemplateRecoveryPolicy, TemplateService
from markweave.templates.validation import ValidatedTemplate
from tests.sqlite_compatibility import enforce_sqlite_334_update_grammar

pytestmark = pytest.mark.integration


def _validated(data: bytes) -> ValidatedTemplate:
    return ValidatedTemplate(
        hashlib.sha256(data).hexdigest(),
        ("word/document.xml",),
        ("Calibri",),
        ("Calibri",),
        (("Calibri", "Carlito"),),
    )


def _user(role: Role, name: str) -> User:
    return User(uuid4(), name, name.casefold(), "hash", role)


def _service(tmp_path: Path) -> tuple[TemplateService, Engine, User, User, User]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    enforce_sqlite_334_update_grammar(engine)
    upgrade_database(engine)
    owner = _user(Role.USER, "Owner")
    other = _user(Role.USER, "Other")
    admin = _user(Role.ADMIN, "Admin")
    with Session(engine) as database, database.begin():
        for user in (owner, other, admin):
            database.add(
                UserRow(
                    id=str(user.id),
                    username=user.username,
                    normalized_username=user.normalized_username,
                    password_hash=user.password_hash,
                    role=user.role.value,
                    active=True,
                    auth_version=0,
                )
            )
    service = TemplateService(
        catalog=SqlTemplateCatalogRepository(engine),
        selections=SqlTemplateSelectionRepository(engine),
        objects=FilesystemObjectStore(tmp_path),
        validate_content=lambda data, _declaration: _validated(data),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    return service, engine, owner, other, admin


def test_version_lifecycle_is_immutable_atomic_audited_and_guarded(
    tmp_path: Path,
) -> None:
    service, engine, owner, other, admin = _service(tmp_path)
    template, first = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Finance", "Quarterly"),
        b"version-one",
        ("Calibri",),
    )
    assert service.download(other, template.id)[2] == b"version-one"

    updated, second = service.replace(
        owner,
        template.id,
        expected_revision=1,
        content=b"version-two",
        expected_fonts=("Calibri",),
    )
    assert updated.revision == 2
    assert service.download(other, template.id, first.id)[2] == b"version-one"
    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(
            update(TemplateVersionRow)
            .where(TemplateVersionRow.id == str(first.id))
            .values(sha256="f" * 64)
        )
    with pytest.raises(TemplateConflictError):
        service.replace(
            owner,
            template.id,
            expected_revision=1,
            content=b"losing-write",
            expected_fonts=("Calibri",),
        )

    restored, third = service.restore(admin, template.id, first.id, expected_revision=2)
    assert restored.revision == 3
    assert third.restored_from_version_id == first.id
    assert third.id != first.id
    assert service.download(other, template.id)[2] == b"version-one"
    frozen, frozen_content = service.resolve_frozen_version(template.id, second.id)
    assert frozen.id == second.id
    assert frozen_content == b"version-two"

    renamed = service.update_metadata(
        admin,
        template.id,
        expected_revision=3,
        name="Renamed",
        description="Still stable",
    )
    assert renamed.owner_id == owner.id
    archived = service.archive(owner, template.id, expected_revision=4)
    assert archived.status is TemplateStatus.ARCHIVED
    with pytest.raises(TemplateUnavailableError):
        service.download(other, template.id)

    service.delete(owner, template.id, expected_revision=5)
    with pytest.raises(TemplateUnavailableError):
        service.download(owner, template.id)
    with Session(engine) as database:
        assert (
            database.scalar(select(func.count()).select_from(TemplateVersionRow)) == 0
        )
        audits = tuple(
            database.scalars(
                select(TemplateAuditRow).order_by(
                    TemplateAuditRow.created_at, TemplateAuditRow.id
                )
            )
        )
        assert len(audits) == 6
        assert sum(record.administrator_intervention for record in audits) == 2


def test_delete_is_guarded_by_preference_and_archive_state(tmp_path: Path) -> None:
    service, _engine, owner, _other, _admin = _service(tmp_path)
    template, _version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Guarded", "Selected"),
        b"content",
        ("Calibri",),
    )
    with pytest.raises(TemplateConflictError):
        service.delete(owner, template.id, expected_revision=1)
    service.set_preferred(owner, template.id)
    service.archive(owner, template.id, expected_revision=1)
    with pytest.raises(TemplateConflictError):
        service.delete(owner, template.id, expected_revision=2)


def test_template_reads_detect_tampering_before_download_restore_or_processing(
    tmp_path: Path,
) -> None:
    service, _engine, owner, _other, _admin = _service(tmp_path)
    template, version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Integrity", "Verified"),
        b"original",
        ("Calibri",),
    )
    FilesystemObjectStore(tmp_path).put(
        ObjectKey(ObjectScope.TEMPLATE_VERSION, owner.id, version.id), b"tampered"
    )

    with pytest.raises(TemplateIntegrityError):
        service.download(owner, template.id, version.id)
    with pytest.raises(TemplateIntegrityError):
        service.restore(owner, template.id, version.id, expected_revision=1)
    with pytest.raises(TemplateIntegrityError):
        service.resolve_frozen_version(template.id, version.id)


def test_database_rejects_owner_restore_and_current_template_corruption(
    tmp_path: Path,
) -> None:
    service, engine, owner, other, _admin = _service(tmp_path)
    first, first_version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "First", "Owner"),
        b"first",
        ("Calibri",),
    )
    second, _second_version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Second", "Owner"),
        b"second",
        ("Calibri",),
    )

    def row(
        *, object_owner: User = owner, restored_from: str | None = None
    ) -> TemplateVersionRow:
        return TemplateVersionRow(
            id=str(uuid4()),
            template_id=str(second.id),
            version_number=2,
            object_owner_id=str(object_owner.id),
            sha256="a" * 64,
            size=1,
            created_at=datetime.now(UTC),
            created_by=str(owner.id),
            restored_from_version_id=restored_from,
            declared_fonts='["Calibri"]',
            resolved_fonts='[["Calibri","Carlito"]]',
            validation_trace='["static_ooxml"]',
            publication_state="pending",
        )

    with pytest.raises(SQLAlchemyError), Session(engine) as database, database.begin():
        database.add(row(object_owner=other))
        database.flush()
    with pytest.raises(SQLAlchemyError), Session(engine) as database, database.begin():
        database.add(row(restored_from=str(first_version.id)))
        database.flush()
    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(
            update(TemplateRow)
            .where(TemplateRow.id == str(second.id))
            .values(current_version_id=str(first.current_version_id))
        )
    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(
            update(TemplateVersionRow)
            .where(TemplateVersionRow.id == str(first_version.id))
            .values(publication_state="pending")
        )
    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(
            update(TemplateRow)
            .where(TemplateRow.id == str(first.id))
            .values(publication_state="pending")
        )


def test_preference_set_and_clear_are_audited_with_prior_target(tmp_path: Path) -> None:
    service, engine, owner, _other, _admin = _service(tmp_path)
    template, _version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Preferred", "Audited"),
        b"preferred",
        ("Calibri",),
    )
    service.set_preferred(owner, template.id)
    service.clear_preferred(owner)

    with Session(engine) as database:
        records = tuple(
            database.scalars(
                select(TemplateAuditRow)
                .where(
                    TemplateAuditRow.operation.in_(("set_preferred", "clear_preferred"))
                )
                .order_by(TemplateAuditRow.created_at, TemplateAuditRow.id)
            )
        )
    assert {record.operation for record in records} == {
        "set_preferred",
        "clear_preferred",
    }
    assert all(record.template_id == str(template.id) for record in records)
    assert all(record.owner_id == str(owner.id) for record in records)


def test_concurrent_filesystem_replacement_has_one_winner_and_no_loser_object(
    tmp_path: Path,
) -> None:
    service, _engine, owner, _other, _admin = _service(tmp_path)
    template, _version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Concurrent", "CAS"),
        b"first",
        ("Calibri",),
    )

    def replace_content(content: bytes) -> object:
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
        outcomes = tuple(executor.map(replace_content, (b"second-a", b"second-b")))

    assert sum(outcome is not None for outcome in outcomes) == 1
    assert len(service.list_versions(owner, template.id)) == 2
    assert service.reclaim_pending() == 0
    object_files = tuple((tmp_path / "objects" / "template-versions").rglob("*"))
    assert sum(path.is_file() for path in object_files) == 2


def test_submission_races_template_mutations_without_dangling_pairs(
    tmp_path: Path,
) -> None:
    service, engine, owner, _other, _admin = _service(tmp_path)
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
            datetime.now(UTC),
        )

    template, first = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Race replace", "Frozen pair"),
        b"first",
        ("Calibri",),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        submitted = executor.submit(jobs.create, submission(template.id, first.id))
        replaced = executor.submit(
            service.replace,
            owner,
            template.id,
            expected_revision=1,
            content=b"second",
            expected_fonts=("Calibri",),
        )
    try:
        job, _ = submitted.result()
    except JobRequestError:
        job = None
    replaced.result()
    if job is not None:
        persisted = jobs.get(job.id)
        assert persisted is not None
        assert persisted.template_version_id == first.id
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(ConversionJobRow.id == str(job.id))
            )

    template, first = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Race archive", "Frozen pair"),
        b"first",
        ("Calibri",),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        submitted = executor.submit(jobs.create, submission(template.id, first.id))
        archived = executor.submit(
            service.archive, owner, template.id, expected_revision=1
        )
    archived.result()
    try:
        job, _ = submitted.result()
    except JobRequestError:
        job = None
    if job is not None:
        persisted = jobs.get(job.id)
        assert persisted is not None
        assert persisted.template_version_id == first.id
        with engine.begin() as connection:
            connection.execute(
                delete(ConversionJobRow).where(ConversionJobRow.id == str(job.id))
            )

    template, first = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Race delete", "Archived pair"),
        b"first",
        ("Calibri",),
    )
    service.archive(owner, template.id, expected_revision=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        submitted = executor.submit(jobs.create, submission(template.id, first.id))
        deleted = executor.submit(
            service.delete, owner, template.id, expected_revision=2
        )
    deleted.result()
    with pytest.raises(JobRequestError):
        submitted.result()
    with Session(engine) as database:
        assert (
            database.scalar(
                select(func.count())
                .select_from(ConversionJobRow)
                .where(ConversionJobRow.template_id == str(template.id))
            )
            == 0
        )
    engine.dispose()


def test_concurrent_reconcilers_claim_one_expired_publication(tmp_path: Path) -> None:
    service, engine, owner, _other, _admin = _service(tmp_path)
    now = datetime(2026, 8, 24, tzinfo=UTC)
    template = TemplateIdentity(
        uuid4(), owner.id, "Expired", "Publication lease", TemplateStatus.ACTIVE
    )
    version = TemplateVersion(
        uuid4(),
        template.id,
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
    SqlTemplateCatalogRepository(engine).reserve_create(template, version)
    other = TemplateService(
        catalog=SqlTemplateCatalogRepository(engine),
        selections=SqlTemplateSelectionRepository(engine),
        objects=FilesystemObjectStore(tmp_path),
        validate_content=lambda data, _declaration: _validated(data),
        clock=lambda: now,
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                lambda candidate: candidate.reclaim_pending(), (service, other)
            )
        )
    assert sorted(outcomes) == [0, 1]
    assert SqlTemplateCatalogRepository(engine).get(template.id) is None
    engine.dispose()
