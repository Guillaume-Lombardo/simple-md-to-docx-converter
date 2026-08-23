"""Real PostgreSQL template ownership and selection integration coverage."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.exc import SQLAlchemyError

from md_converter.auth.models import Role, User
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import (
    SessionRow,
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
    UserRow,
)
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.templates.models import TemplateIdentity, TemplateStatus
from tests.storage_contracts import (
    exercise_template_repository_contract,
    exercise_template_service_contract,
)


def clear_template_test_data(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(SystemTemplateSelectionRow))
        connection.execute(delete(TemplatePreferenceRow))
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
