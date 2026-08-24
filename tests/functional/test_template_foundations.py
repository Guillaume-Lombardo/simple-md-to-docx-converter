"""Functional template visibility, authorization, and selection behavior."""

from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.auth.errors import AuthenticationError
from md_converter.auth.models import Role, User
from md_converter.templates.errors import TemplateUnavailableError
from md_converter.templates.models import (
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
)
from md_converter.templates.service import TemplateOperation, TemplateService


def user(name: str, role: Role = Role.USER) -> User:
    return User(uuid4(), name, name.casefold(), "hash:" + name, role)


@pytest.mark.functional
def test_template_service_enforces_two_user_and_administrator_boundaries(
    mocker: MockerFixture,
) -> None:
    admin = user("Admin", Role.ADMIN)
    alice = user("Alice")
    bob = user("Bob")
    active = TemplateIdentity(
        uuid4(), alice.id, "Shared", "Visible", TemplateStatus.ACTIVE
    )
    archived = TemplateIdentity(
        uuid4(), alice.id, "Private", "Archived", TemplateStatus.ARCHIVED
    )
    templates = {active.id: active, archived.id: archived}
    catalog = mocker.Mock()
    catalog.get.side_effect = templates.get
    page = TemplatePage((active,), 1, 0, 20)
    catalog.search.return_value = page
    selections = mocker.Mock()
    selections.resolve.return_value = active
    service = TemplateService(catalog=catalog, selections=selections)

    query = TemplateSearch(name="shared")
    assert service.search(bob, query) == page
    catalog.search.assert_called_once_with(
        query, viewer_id=bob.id, viewer_is_admin=False
    )
    assert service.get_visible(bob, active.id) == active
    with pytest.raises(TemplateUnavailableError):
        service.get_visible(bob, archived.id)
    assert service.get_visible(alice, archived.id) == archived
    assert service.get_visible(admin, archived.id) == archived

    owner_authorization = service.authorize_mutation(alice, archived.id)
    assert owner_authorization.owner_id == alice.id
    assert not owner_authorization.administrator_intervention
    with pytest.raises(AuthenticationError):
        service.authorize_mutation(bob, archived.id)
    admin_authorization = service.authorize_mutation(admin, archived.id)
    assert admin_authorization.operation is TemplateOperation.MUTATE
    assert admin_authorization.administrator_intervention
    with pytest.raises(TemplateUnavailableError):
        service.authorize_mutation(admin, uuid4())

    service.set_preferred(bob, active.id)
    selections.set_preferred_audited.assert_called_once()
    assert selections.set_preferred_audited.call_args.args[:2] == (bob.id, active.id)
    assert service.resolve(bob) == active
    selections.resolve.assert_called_once_with(bob.id)
    service.clear_preferred(bob)
    selections.clear_preferred_audited.assert_called_once()
    assert selections.clear_preferred_audited.call_args.args[0] == bob.id

    with pytest.raises(AuthenticationError):
        service.set_system_fallback(bob, active.id)
    fallback_authorization = service.set_system_fallback(admin, active.id)
    selections.set_system_fallback.assert_called_once_with(active.id)
    assert fallback_authorization.operation is TemplateOperation.SET_SYSTEM_FALLBACK
    assert fallback_authorization.administrator_intervention
    with pytest.raises(TemplateUnavailableError):
        service.set_system_fallback(admin, archived.id)
