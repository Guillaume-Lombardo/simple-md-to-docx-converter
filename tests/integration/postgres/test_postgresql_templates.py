"""Real PostgreSQL template ownership and selection integration coverage."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.exc import SQLAlchemyError

from md_converter.auth.models import Role, User
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import (
    SessionRow,
    SystemTemplateSelectionRow,
    TemplateAuditRow,
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.storage import ObjectKey, ObjectScope, S3ObjectStore
from md_converter.templates.errors import TemplateConflictError
from md_converter.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplateStatus,
)
from md_converter.templates.service import TemplateService
from tests.storage_contracts import (
    exercise_template_repository_contract,
    exercise_template_service_contract,
)


def clear_template_test_data(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(TemplateAuditRow))
        connection.execute(delete(SystemTemplateSelectionRow))
        connection.execute(delete(TemplatePreferenceRow))
        connection.execute(delete(TemplateVersionRow))
        connection.execute(delete(TemplateRow))
        connection.execute(delete(SessionRow))
        connection.execute(delete(UserRow))


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_template_contract_constraints_and_immutability() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
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
def test_distributed_template_versions_and_concurrent_replacement() -> None:
    engine = create_database_engine(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    clear_template_test_data(engine)
    owner = User(uuid4(), "Owner", f"owner-{uuid4()}", "hash", Role.USER)
    SqlUserRepository(engine).create(owner)
    objects = S3ObjectStore(
        boto3.client(
            "s3",
            endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
            region_name=os.environ.get("MD_CONVERTER_TEST_S3_REGION", "us-east-1"),
            aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
        ),
        os.environ["MD_CONVERTER_TEST_S3_BUCKET"],
    )
    service = TemplateService(
        catalog=SqlTemplateCatalogRepository(engine),
        selections=SqlTemplateSelectionRepository(engine),
        objects=objects,
        validate_content=lambda data: hashlib.sha256(data).hexdigest(),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
    )
    try:
        template, first = service.create_versioned(
            owner, TemplateCreate(uuid4(), "Distributed", "RustFS"), b"first"
        )
        with pytest.raises(SQLAlchemyError), engine.begin() as connection:
            connection.execute(
                update(TemplateVersionRow)
                .where(TemplateVersionRow.id == str(first.id))
                .values(sha256="f" * 64)
            )

        def replace(content: bytes) -> object:
            try:
                return service.replace(
                    owner, template.id, expected_revision=1, content=content
                )
            except TemplateConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(replace, (b"second-a", b"second-b")))
        assert sum(outcome is not None for outcome in outcomes) == 1
        versions = service.list_versions(owner, template.id)
        assert len(versions) == 2
        assert service.download(owner, template.id, first.id)[2] == b"first"
        current = service.download(owner, template.id)[2]
        assert current in {b"second-a", b"second-b"}
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
    finally:
        clear_template_test_data(engine)
        engine.dispose()
