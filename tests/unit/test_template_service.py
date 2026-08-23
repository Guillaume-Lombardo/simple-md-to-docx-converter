"""Unit branch coverage for template visibility and authorization."""

from dataclasses import fields
from typing import cast
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.auth.errors import AuthenticationError
from md_converter.auth.models import Role, User
from md_converter.templates.errors import TemplateUnavailableError
from md_converter.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplateStatus,
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
