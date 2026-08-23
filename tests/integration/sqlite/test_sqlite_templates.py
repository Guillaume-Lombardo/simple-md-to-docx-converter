"""Real SQLite template ownership and selection integration coverage."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from md_converter.auth.models import Role, User
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import (
    TemplatePreferenceRow,
    TemplateRow,
    UserRow,
)
from md_converter.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.templates.models import TemplateIdentity, TemplateStatus
from tests.storage_contracts import exercise_template_repository_contract


@pytest.mark.integration
def test_sqlite_template_repository_contract_and_restart(tmp_path: Path) -> None:
    database_url = standalone_database_url(tmp_path)
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    exercise_template_repository_contract(
        SqlUserRepository(engine),
        SqlTemplateCatalogRepository(engine),
        SqlTemplateSelectionRepository(engine),
    )
    with engine.connect() as connection:
        template_count = connection.scalar(
            select(func.count()).select_from(TemplateRow)
        )
        preference_count = connection.scalar(
            select(func.count()).select_from(TemplatePreferenceRow)
        )
    engine.dispose()

    reopened = create_database_engine(database_url)
    with reopened.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(TemplateRow))
            == template_count
        )
        assert (
            connection.scalar(select(func.count()).select_from(TemplatePreferenceRow))
            == preference_count
        )
    reopened.dispose()


@pytest.mark.integration
def test_sqlite_template_constraints_immutability_and_write_failure(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    catalog = SqlTemplateCatalogRepository(engine)
    selections = SqlTemplateSelectionRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    other = User(uuid4(), "Other", "other", "hash:other", Role.USER)
    users.create(owner)
    users.create(other)
    template = TemplateIdentity(
        uuid4(), owner.id, "Immutable", "Owner remains fixed", TemplateStatus.ACTIVE
    )
    fallback = TemplateIdentity(
        uuid4(), other.id, "Fallback", "System fallback", TemplateStatus.ACTIVE
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
                uuid4(), uuid4(), "Orphan", "Missing owner", TemplateStatus.ACTIVE
            )
        )
    with pytest.raises(PersistenceError):
        selections.set_preferred(uuid4(), fallback.id)

    with engine.connect() as connection:
        connection.execute(text("PRAGMA query_only=ON"))
    with pytest.raises(PersistenceError) as caught:
        catalog.add(
            TemplateIdentity(
                uuid4(), other.id, "Blocked", "write failure", TemplateStatus.ACTIVE
            )
        )
    assert "Blocked" not in repr(caught.value)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA query_only=OFF"))
    engine.dispose()
