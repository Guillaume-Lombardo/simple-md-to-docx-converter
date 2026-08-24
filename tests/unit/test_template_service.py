"""Unit branch coverage for template visibility and authorization."""

import hashlib
from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.auth.errors import AuthenticationError
from md_converter.auth.models import Role, User
from md_converter.persistence.errors import PersistenceError
from md_converter.storage import ObjectNotFoundError, ObjectStore, ObjectStoreError
from md_converter.templates.errors import (
    TemplateConflictError,
    TemplateStorageError,
    TemplateUnavailableError,
)
from md_converter.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplateStatus,
    TemplateVersion,
)
from md_converter.templates.service import TemplateOperation, TemplateService


@pytest.mark.unit
def test_template_service_visibility_selection_and_audit_context(
    mocker: MockerFixture,
) -> None:
    admin = User(uuid4(), "Admin", "admin", "hash:admin", Role.ADMIN)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    outsider = User(uuid4(), "Outsider", "outsider", "hash:outsider", Role.USER)
    active = TemplateIdentity(
        uuid4(), owner.id, "Active", "Shared", TemplateStatus.ACTIVE
    )
    archived = TemplateIdentity(
        uuid4(), owner.id, "Archived", "Private", TemplateStatus.ARCHIVED
    )
    catalog = mocker.Mock()
    catalog.get.side_effect = {active.id: active, archived.id: archived}.get
    selections = mocker.Mock()
    selections.resolve.return_value = active
    service = TemplateService(catalog=catalog, selections=selections)

    assert service.get_visible(outsider, active.id) == active
    with pytest.raises(TemplateUnavailableError):
        service.get_visible(outsider, archived.id)
    assert service.get_visible(owner, archived.id) == archived
    assert service.get_visible(admin, archived.id) == archived
    with pytest.raises(TemplateUnavailableError):
        service.get_visible(admin, uuid4())
    assert not service.authorize_mutation(owner, archived.id).administrator_intervention
    with pytest.raises(AuthenticationError):
        service.authorize_mutation(outsider, archived.id)
    assert service.authorize_mutation(admin, archived.id).administrator_intervention
    with pytest.raises(TemplateUnavailableError):
        service.authorize_mutation(admin, uuid4())

    service.set_preferred(owner, active.id)
    service.clear_preferred(owner)
    assert service.resolve(owner) == active
    with pytest.raises(AuthenticationError):
        service.set_system_fallback(owner, active.id)
    authorization = service.set_system_fallback(admin, active.id)
    assert authorization.operation is TemplateOperation.SET_SYSTEM_FALLBACK
    assert authorization.administrator_intervention
    with pytest.raises(TemplateUnavailableError):
        service.set_system_fallback(admin, archived.id)


@pytest.mark.unit
def test_template_creation_always_derives_owner_from_actor(
    mocker: MockerFixture,
) -> None:
    actor = User(uuid4(), "Actor", "actor", "hash:actor", Role.USER)
    forged_owner = User(uuid4(), "Forged", "forged", "hash:forged", Role.USER)
    forged_request = TemplateIdentity(
        uuid4(),
        forged_owner.id,
        "Owned by actor",
        "The supplied owner must be ignored",
        TemplateStatus.ACTIVE,
    )
    catalog = mocker.Mock()
    service = TemplateService(catalog=catalog, selections=mocker.Mock())

    assert {field.name for field in fields(TemplateCreate)} == {
        "id",
        "name",
        "description",
        "status",
    }
    created = service.create(actor, cast(TemplateCreate, forged_request))

    assert created.owner_id == actor.id
    assert created.owner_id != forged_owner.id
    catalog.add.assert_called_once_with(created)


@pytest.mark.unit
def test_versioned_service_delegates_complete_immutable_lifecycle(
    mocker: MockerFixture,
) -> None:
    owner = User(uuid4(), "Owner", "owner", "hash", Role.USER)
    template_id = uuid4()
    catalog = mocker.Mock()
    selections = mocker.Mock()
    objects = mocker.Mock(spec=ObjectStore)
    service = TemplateService(
        catalog=catalog,
        selections=selections,
        objects=objects,
        validate_content=lambda data: hashlib.sha256(data).hexdigest(),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
    )
    catalog.create_versioned.side_effect = lambda template, _version, _audit: template
    created, first = service.create_versioned(
        owner, TemplateCreate(template_id, "Name", "Description"), b"first"
    )
    assert created.current_version_id == first.id
    assert first.number == 1

    catalog.get.return_value = created
    catalog.list_versions.return_value = (first,)
    catalog.publish_version.side_effect = lambda _template_id, **values: replace(
        created,
        revision=values["expected_revision"] + 1,
        current_version_id=values["version"].id,
    )
    replaced, second = service.replace(
        owner, template_id, expected_revision=1, content=b"second"
    )
    assert replaced.revision == 2
    assert second.number == 2

    catalog.get.return_value = replaced
    catalog.get_version.return_value = first
    catalog.list_versions.return_value = (second, first)
    objects.get.return_value = b"first"
    restored, third = service.restore(owner, template_id, first.id, expected_revision=2)
    assert restored.revision == 3
    assert third.number == 3
    assert third.restored_from_version_id == first.id

    catalog.update_metadata.return_value = replace(
        restored, name="Renamed", description="Updated", revision=4
    )
    renamed = service.update_metadata(
        owner,
        template_id,
        expected_revision=3,
        name="Renamed",
        description="Updated",
    )
    assert renamed.name == "Renamed"
    catalog.set_status.return_value = replace(
        renamed, status=TemplateStatus.ARCHIVED, revision=5
    )
    assert (
        service.archive(owner, template_id, expected_revision=4).status
        is TemplateStatus.ARCHIVED
    )

    catalog.get.return_value = created
    catalog.list_versions.return_value = (third, second, first)
    assert service.list_versions(owner, template_id)[0] == third
    catalog.get_version.return_value = second
    assert service.download(owner, template_id, second.id)[2] == b"first"
    assert service.resolve_frozen_version(template_id, second.id)[0] == second

    catalog.delete_guarded.return_value = (first, second, third)
    service.delete(owner, template_id, expected_revision=5)
    assert objects.delete.call_count >= 3


@pytest.mark.unit
def test_versioned_service_compensates_and_sanitizes_storage_failures(  # noqa: PLR0915
    mocker: MockerFixture,
) -> None:
    owner = User(uuid4(), "Owner", "owner", "hash", Role.USER)
    template = TemplateIdentity(
        uuid4(), owner.id, "Name", "Description", TemplateStatus.ACTIVE
    )
    catalog = mocker.Mock()
    catalog.get.return_value = template
    catalog.list_versions.return_value = ()
    objects = mocker.Mock(spec=ObjectStore)
    selections = mocker.Mock()
    service = TemplateService(
        catalog=catalog,
        selections=selections,
        objects=objects,
        validate_content=lambda data: hashlib.sha256(data).hexdigest(),
    )
    catalog.publish_version.side_effect = PersistenceError
    with pytest.raises(PersistenceError):
        service.replace(owner, template.id, expected_revision=1, content=b"content")
    objects.delete.assert_called_once()

    objects.reset_mock()
    objects.put.side_effect = ObjectStoreError("private marker")
    with pytest.raises(TemplateStorageError) as caught:
        service.replace(owner, template.id, expected_revision=1, content=b"secret")
    assert "private" not in repr(caught.value)

    objects.put.side_effect = None
    objects.get.side_effect = ObjectNotFoundError
    version = TemplateVersion(
        uuid4(),
        template.id,
        1,
        owner.id,
        "a" * 64,
        1,
        datetime.now(UTC),
        owner.id,
    )
    catalog.get_version.return_value = version
    with pytest.raises(TemplateStorageError):
        service.download(owner, template.id, version.id)

    unconfigured = TemplateService(catalog=catalog, selections=mocker.Mock())
    with pytest.raises(RuntimeError, match="not configured"):
        unconfigured.download(owner, template.id)
    catalog.get_version.return_value = None
    with pytest.raises(TemplateUnavailableError):
        service.resolve_frozen_version(template.id, uuid4())

    catalog.get.return_value = replace(template, status=TemplateStatus.ARCHIVED)
    with pytest.raises(TemplateConflictError):
        service.replace(owner, template.id, expected_revision=1, content=b"content")

    catalog.get.return_value = template
    catalog.get_version.return_value = None
    with pytest.raises(TemplateUnavailableError):
        service.restore(owner, template.id, uuid4(), expected_revision=1)
    catalog.get.return_value = replace(template, status=TemplateStatus.ARCHIVED)
    catalog.get_version.return_value = version
    with pytest.raises(TemplateUnavailableError):
        service.restore(owner, template.id, version.id, expected_revision=1)

    catalog.get.return_value = template
    with pytest.raises(TemplateUnavailableError):
        service.download(owner, template.id)
    catalog.get.return_value = replace(template, current_version_id=version.id)
    catalog.get_version.return_value = None
    with pytest.raises(TemplateUnavailableError):
        service.download(owner, template.id)

    admin = replace(owner, role=Role.ADMIN)
    catalog.get.return_value = template
    service.set_system_fallback(admin, template.id)
    selections.set_system_fallback_audited.assert_called_once()
