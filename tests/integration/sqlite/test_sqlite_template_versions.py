"""Real SQLite/filesystem coverage for versioned template transactions."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from md_converter.auth.models import Role, User
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import (
    TemplateAuditRow,
    TemplateVersionRow,
    UserRow,
)
from md_converter.persistence.sql import create_database_engine
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.storage import FilesystemObjectStore
from md_converter.templates.errors import (
    TemplateConflictError,
    TemplateUnavailableError,
)
from md_converter.templates.models import TemplateCreate, TemplateStatus
from md_converter.templates.service import TemplateService

pytestmark = pytest.mark.integration


def _user(role: Role, name: str) -> User:
    return User(uuid4(), name, name.casefold(), "hash", role)


def _service(tmp_path: Path) -> tuple[TemplateService, Engine, User, User, User]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
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
        validate_content=lambda data: hashlib.sha256(data).hexdigest(),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
    )
    return service, engine, owner, other, admin


def test_version_lifecycle_is_immutable_atomic_audited_and_guarded(
    tmp_path: Path,
) -> None:
    service, engine, owner, other, admin = _service(tmp_path)
    template, first = service.create_versioned(
        owner, TemplateCreate(uuid4(), "Finance", "Quarterly"), b"version-one"
    )
    assert service.download(other, template.id)[2] == b"version-one"

    updated, second = service.replace(
        owner, template.id, expected_revision=1, content=b"version-two"
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
            owner, template.id, expected_revision=1, content=b"losing-write"
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
        owner, TemplateCreate(uuid4(), "Guarded", "Selected"), b"content"
    )
    with pytest.raises(TemplateConflictError):
        service.delete(owner, template.id, expected_revision=1)
    service.set_preferred(owner, template.id)
    service.archive(owner, template.id, expected_revision=1)
    with pytest.raises(TemplateConflictError):
        service.delete(owner, template.id, expected_revision=2)
