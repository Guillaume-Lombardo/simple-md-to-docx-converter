"""Unit coverage for template SQL failures and Alembic structure."""

import importlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

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
from md_converter.templates.errors import (
    TemplateConflictError,
    TemplateUnavailableError,
)
from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplateSearch,
    TemplateStatus,
    TemplateVersion,
)

REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260823_02_template_identity"
)
VERSION_REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260824_04_template_versions"
)
INTEGRITY_REVISION: Any = importlib.import_module(
    "md_converter.persistence.migrations.versions.20260824_05_template_integrity"
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
def test_inprocess_versioned_template_compare_and_swap_and_guards() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    catalog = SqlTemplateCatalogRepository(engine)
    owner = User(uuid4(), "Owner", "owner-versioned", "hash", Role.USER)
    users.create(owner)
    template_id = uuid4()
    first_id = uuid4()
    template = TemplateIdentity(
        template_id,
        owner.id,
        "Versioned",
        "Initial",
        TemplateStatus.ACTIVE,
        1,
        first_id,
    )
    first = TemplateVersion(
        first_id,
        template_id,
        1,
        owner.id,
        "a" * 64,
        10,
        datetime.now(UTC),
        owner.id,
    )

    def audit(operation: str, version_id: UUID | None = None) -> TemplateAuditRecord:
        return TemplateAuditRecord(
            uuid4(),
            owner.id,
            owner.id,
            template_id,
            operation,
            version_id,
            False,
            datetime.now(UTC),
        )

    assert (
        catalog.create_versioned(template, first, audit("create", first.id)) == template
    )
    assert catalog.get_version(template_id, first.id) == first
    assert catalog.get_version(template_id, uuid4()) is None
    assert catalog.list_versions(template_id) == (first,)

    renamed = catalog.update_metadata(
        template_id,
        expected_revision=1,
        name="Renamed",
        description="Updated",
        audit=audit("update_metadata"),
    )
    assert renamed.revision == 2
    with pytest.raises(TemplateConflictError):
        catalog.update_metadata(
            template_id,
            expected_revision=1,
            name="Lost",
            description="Race",
            audit=audit("update_metadata"),
        )
    second = TemplateVersion(
        uuid4(),
        template_id,
        2,
        owner.id,
        "b" * 64,
        20,
        datetime.now(UTC),
        owner.id,
        first.id,
    )
    published = catalog.publish_version(
        template_id,
        expected_revision=2,
        version=second,
        audit=audit("replace", second.id),
    )
    assert published.current_version_id == second.id
    assert catalog.list_versions(template_id) == (second, first)
    with pytest.raises(TemplateConflictError):
        catalog.publish_version(
            template_id,
            expected_revision=2,
            version=second,
            audit=audit("replace", second.id),
        )

    archived = catalog.set_status(
        template_id,
        expected_revision=3,
        status=TemplateStatus.ARCHIVED.value,
        audit=audit("archive"),
    )
    assert archived.status is TemplateStatus.ARCHIVED
    deleted = catalog.delete_guarded(
        template_id, expected_revision=4, audit=audit("delete")
    )
    assert deleted == (first, second)
    assert catalog.get(template_id) is None
    with pytest.raises(TemplateConflictError):
        catalog.delete_guarded(template_id, expected_revision=4, audit=audit("delete"))
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


@pytest.mark.unit
@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_version_migration_defines_immutable_history_and_audit(
    mocker: MockerFixture, dialect: str
) -> None:
    operations = mocker.patch.object(VERSION_REVISION, "op")
    operations.get_bind.return_value.dialect.name = dialect
    VERSION_REVISION.upgrade()
    assert operations.create_table.call_count == 2
    assert operations.create_index.call_count == 2
    assert any(
        "template_versions_immutable" in call.args[0]
        for call in operations.execute.call_args_list
    )
    operations.reset_mock()
    operations.get_bind.return_value.dialect.name = dialect
    VERSION_REVISION.downgrade()
    assert operations.drop_table.call_count == 2
    assert operations.drop_index.call_count == 2
    assert operations.drop_column.call_count == 2
    assert operations.execute.called


@pytest.mark.unit
@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_integrity_migration_adds_publication_evidence_and_triggers(
    mocker: MockerFixture, dialect: str
) -> None:
    operations = mocker.patch.object(INTEGRITY_REVISION, "op")
    operations.get_bind.return_value.dialect.name = dialect

    INTEGRITY_REVISION.upgrade()

    assert operations.add_column.call_count == 5
    assert operations.batch_alter_table.call_count == 2
    executed = "\n".join(call.args[0] for call in operations.execute.call_args_list)
    assert "template_versions_immutable" in executed
    assert "conversion_template_integrity" in executed

    operations.reset_mock()
    operations.get_bind.return_value.dialect.name = dialect
    INTEGRITY_REVISION.downgrade()
    assert operations.batch_alter_table.call_count == 2
    assert operations.execute.called
