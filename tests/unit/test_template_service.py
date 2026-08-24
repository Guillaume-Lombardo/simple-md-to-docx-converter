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
    TemplateIntegrityError,
    TemplateStorageError,
    TemplateUnavailableError,
)
from md_converter.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)
from md_converter.templates.service import (
    TemplateOperation,
    TemplateRecoveryPolicy,
    TemplateService,
)
from md_converter.templates.validation import ValidatedTemplate


def _validated(data: bytes) -> ValidatedTemplate:
    return ValidatedTemplate(
        hashlib.sha256(data).hexdigest(),
        ("word/document.xml",),
        ("Calibri",),
        ("Calibri",),
        (("Calibri", "Carlito"),),
    )


@pytest.mark.unit
def test_template_recovery_policy_rejects_invalid_duration() -> None:
    for duration in (0, -1, True, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite and positive"):
            TemplateRecoveryPolicy(duration)


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
    selections.preferred_id.return_value = active.id
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
    assert service.selection_label(owner, active) == "Preferred template"
    selections.preferred_id.return_value = None
    assert service.selection_label(owner, active) == "System fallback template"
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
        validate_content=lambda data, _declaration: _validated(data),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    catalog.reserve_create.return_value = None
    catalog.finalize_version.side_effect = lambda _template_id, **values: (
        TemplateIdentity(
            template_id,
            owner.id,
            "Name",
            "Description",
            TemplateStatus.ACTIVE,
            current_version_id=values["version_id"],
        )
    )
    created, first = service.create_versioned(
        owner,
        TemplateCreate(template_id, "Name", "Description"),
        b"first",
        ("Calibri",),
    )
    assert created.current_version_id == first.id
    assert first.number == 1

    catalog.get.return_value = created
    catalog.list_versions.return_value = (first,)
    catalog.reserve_version.side_effect = lambda _template_id, **values: replace(
        values["version"],
        number=3 if values["version"].restored_from_version_id else 2,
    )
    catalog.finalize_version.side_effect = lambda _template_id, **values: replace(
        created,
        revision=values["expected_revision"] + 1,
        current_version_id=values["version_id"],
    )
    replaced, second = service.replace(
        owner,
        template_id,
        expected_revision=1,
        content=b"second",
        expected_fonts=("Calibri",),
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
    objects.get.return_value = b"second"
    assert service.download(owner, template_id, second.id)[2] == b"second"
    assert service.resolve_frozen_version(template_id, second.id)[0] == second

    catalog.begin_delete.return_value = (first, second, third)
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
        validate_content=lambda data, _declaration: _validated(data),
        recovery_policy=TemplateRecoveryPolicy(60),
    )
    catalog.reserve_create.return_value = None
    catalog.finalize_version.side_effect = PersistenceError
    with pytest.raises(PersistenceError):
        service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "Create", "Compensation"),
            b"content",
            ("Calibri",),
        )
    catalog.abort_pending.assert_called_once()
    catalog.abort_pending.reset_mock()
    objects.reset_mock()
    catalog.reserve_version.side_effect = lambda _template_id, **values: values[
        "version"
    ]
    catalog.finalize_version.side_effect = PersistenceError
    with pytest.raises(PersistenceError):
        service.replace(
            owner,
            template.id,
            expected_revision=1,
            content=b"content",
            expected_fonts=("Calibri",),
        )
    objects.delete.assert_called_once()

    objects.reset_mock()
    objects.put.side_effect = ObjectStoreError("private marker")
    with pytest.raises(TemplateStorageError) as caught:
        service.replace(
            owner,
            template.id,
            expected_revision=1,
            content=b"secret",
            expected_fonts=("Calibri",),
        )
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

    objects.get.side_effect = None
    objects.get.return_value = b"x"
    with pytest.raises(TemplateIntegrityError):
        service.resolve_frozen_version(template.id, version.id)

    unconfigured = TemplateService(catalog=catalog, selections=mocker.Mock())
    with pytest.raises(RuntimeError, match="not configured"):
        unconfigured.download(owner, template.id)
    configured_without_policy = TemplateService(
        catalog=catalog,
        selections=mocker.Mock(),
        objects=objects,
        validate_content=lambda data, _declaration: _validated(data),
    )
    with pytest.raises(RuntimeError, match="recovery policy"):
        configured_without_policy.create_versioned(
            owner,
            TemplateCreate(uuid4(), "Name", "Description"),
            b"content",
            ("Calibri",),
        )
    catalog.get_version.return_value = None
    with pytest.raises(TemplateUnavailableError):
        service.resolve_frozen_version(template.id, uuid4())

    catalog.get.return_value = replace(template, status=TemplateStatus.ARCHIVED)
    with pytest.raises(TemplateUnavailableError):
        service.set_preferred(owner, template.id)
    with pytest.raises(TemplateConflictError):
        service.replace(
            owner,
            template.id,
            expected_revision=1,
            content=b"content",
            expected_fonts=("Calibri",),
        )

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


@pytest.mark.unit
def test_pending_object_and_deletion_failures_remain_retryable(
    mocker: MockerFixture,
) -> None:
    owner = User(uuid4(), "Owner", "owner", "hash", Role.USER)
    template = TemplateIdentity(
        uuid4(), owner.id, "Name", "Description", TemplateStatus.ARCHIVED, 2, uuid4()
    )
    version = TemplateVersion(
        template.current_version_id or uuid4(),
        template.id,
        1,
        owner.id,
        hashlib.sha256(b"content").hexdigest(),
        len(b"content"),
        datetime.now(UTC),
        owner.id,
        declared_fonts=("Calibri",),
        resolved_fonts=(("Calibri", "Carlito"),),
        validation_trace=("static_ooxml",),
    )
    catalog = mocker.Mock()
    catalog.get.return_value = replace(template, status=TemplateStatus.ACTIVE)
    catalog.reserve_version.side_effect = lambda _template_id, **values: values[
        "version"
    ]
    catalog.finalize_version.side_effect = PersistenceError
    objects = mocker.Mock(spec=ObjectStore)
    objects.delete.side_effect = ObjectStoreError
    service = TemplateService(
        catalog=catalog,
        selections=mocker.Mock(),
        objects=objects,
        validate_content=lambda data, _declaration: _validated(data),
        recovery_policy=TemplateRecoveryPolicy(60),
    )

    with pytest.raises(PersistenceError):
        service.replace(
            owner,
            template.id,
            expected_revision=1,
            content=b"content",
            expected_fonts=("Calibri",),
        )
    catalog.abort_pending.assert_not_called()

    pending = replace(
        version,
        publication_state=TemplatePublicationState.PENDING,
        publication_token=uuid4(),
        publication_lease_expires_at=datetime.now(UTC),
    )
    catalog.claim_stale_pending.return_value = (pending,)
    catalog.pending_deletions.return_value = ()
    catalog.abort_pending.return_value = True
    objects.delete.side_effect = ObjectStoreError
    assert service.reclaim_pending() == 0
    catalog.release_pending_claim.assert_called_once()

    catalog.release_pending_claim.reset_mock()
    objects.delete.side_effect = None
    assert service.reclaim_pending() == 1
    assert catalog.abort_pending.call_args.args[:2] == (template.id, version.id)
    catalog.abort_pending.return_value = False
    assert service.reclaim_pending() == 0

    catalog.reset_mock()
    objects.reset_mock()
    catalog.get.return_value = template
    catalog.begin_delete.return_value = (version,)
    objects.delete.side_effect = ObjectStoreError
    with pytest.raises(TemplateStorageError):
        service.delete(owner, template.id, expected_revision=2)
    catalog.finalize_delete.assert_not_called()

    objects.delete.side_effect = None
    catalog.claim_stale_pending.return_value = ()
    catalog.pending_deletions.return_value = ((template.id, (version,)),)
    objects.delete.side_effect = ObjectStoreError
    assert service.reclaim_pending() == 0
    catalog.finalize_delete.assert_not_called()
    objects.delete.side_effect = None
    assert service.reclaim_pending() == 1
    catalog.finalize_delete.assert_called_once_with(template.id)

    with pytest.raises(TemplateConflictError):
        service._publication_token(version)
