"""Unit coverage for template SQL failures and Alembic structure."""

import importlib
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy.exc import SQLAlchemyError

from md_converter.auth.models import Role, User
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.templates.errors import TemplateUnavailableError
from md_converter.templates.models import (
    TemplateIdentity,
    TemplateSearch,
    TemplateStatus,
)

REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260823_02_template_identity"
)


@pytest.mark.unit
def test_inprocess_template_repository_control_flow() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    catalog = SqlTemplateCatalogRepository(engine)
    selections = SqlTemplateSelectionRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    viewer = User(uuid4(), "Viewer", "viewer", "hash:viewer", Role.USER)
    users.create(owner)
    users.create(viewer)
    active = TemplateIdentity(
        uuid4(), owner.id, "Shared", "Quarterly résumé", TemplateStatus.ACTIVE
    )
    other = TemplateIdentity(
        uuid4(), viewer.id, "Default", "General", TemplateStatus.ACTIVE
    )
    archived = TemplateIdentity(
        uuid4(), owner.id, "Legacy", "Retired", TemplateStatus.ARCHIVED
    )
    for template in (active, other, archived):
        catalog.add(template)

    assert catalog.get(active.id) == active
    assert catalog.get(uuid4()) is None
    assert catalog.search(
        TemplateSearch(), viewer_id=viewer.id, viewer_is_admin=False
    ).items == (other, active)
    assert catalog.search(
        TemplateSearch(
            name="shared",
            description="RÉSUMÉ",
            owner_id=owner.id,
            status=TemplateStatus.ACTIVE,
            limit=1,
        ),
        viewer_id=viewer.id,
        viewer_is_admin=False,
    ).items == (active,)
    assert catalog.search(
        TemplateSearch(status=TemplateStatus.ARCHIVED),
        viewer_id=viewer.id,
        viewer_is_admin=True,
    ).items == (archived,)

    assert selections.preferred_id(owner.id) is None
    assert selections.system_fallback_id() is None
    assert selections.resolve(owner.id) is None
    selections.set_system_fallback(other.id)
    assert selections.system_fallback_id() == other.id
    assert selections.resolve(owner.id) == other
    selections.set_preferred(owner.id, active.id)
    selections.set_preferred(owner.id, other.id)
    assert selections.preferred_id(owner.id) == other.id
    assert selections.resolve(owner.id) == other
    selections.clear_preferred(owner.id)
    assert selections.preferred_id(owner.id) is None
    with pytest.raises(TemplateUnavailableError):
        selections.set_preferred(owner.id, archived.id)
    engine.dispose()


@pytest.mark.unit
def test_template_repositories_sanitize_every_sqlalchemy_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.MagicMock()
    catalog = SqlTemplateCatalogRepository(engine)
    selections = SqlTemplateSelectionRepository(engine)
    owner_id = uuid4()
    template = TemplateIdentity(
        uuid4(), owner_id, "Private", "secret marker", TemplateStatus.ACTIVE
    )
    mocker.patch(
        "md_converter.persistence.templates.DatabaseSession",
        side_effect=SQLAlchemyError("private SQL and values"),
    )
    operations = (
        lambda: catalog.add(template),
        lambda: catalog.get(template.id),
        lambda: catalog.search(
            TemplateSearch(), viewer_id=owner_id, viewer_is_admin=False
        ),
        lambda: selections.set_preferred(owner_id, template.id),
        lambda: selections.clear_preferred(owner_id),
        lambda: selections.preferred_id(owner_id),
        lambda: selections.set_system_fallback(template.id),
        selections.system_fallback_id,
        lambda: selections.resolve(owner_id),
    )
    for operation in operations:
        with pytest.raises(PersistenceError) as caught:
            operation()
        assert "private" not in repr(caught.value)
        assert caught.value.__suppress_context__


@pytest.mark.unit
@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_template_migration_defines_constraints_indexes_and_owner_trigger(
    mocker: MockerFixture, dialect: str
) -> None:
    operations = mocker.patch.object(REVISION, "op")
    operations.get_bind.return_value.dialect.name = dialect

    REVISION.upgrade()

    assert operations.create_table.call_count == 3
    assert operations.create_index.call_count == 4
    executed = "\n".join(call.args[0] for call in operations.execute.call_args_list)
    assert "templates_owner_immutable" in executed
    if dialect == "postgresql":
        assert "reject_template_owner_change" in executed

    operations.reset_mock()
    operations.get_bind.return_value.dialect.name = dialect
    REVISION.downgrade()
    assert operations.drop_table.call_count == 3
    assert operations.drop_index.call_count == 4
    assert operations.execute.called
