"""Template visibility, selection, and future-mutation authorization services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from md_converter.auth.models import Role, User
from md_converter.auth.service import AuthorizationService
from md_converter.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)
from md_converter.templates.errors import (
    TemplateConflictError,
    TemplateStorageError,
    TemplateUnavailableError,
)
from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateCreate,
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
    TemplateVersion,
)
from md_converter.templates.ports import (
    TemplateCatalogRepository,
    TemplateSelectionRepository,
)


class TemplateOperation(StrEnum):
    """Sensitive operations T15 can persist through its audit adapter."""

    MUTATE = "mutate"
    SET_SYSTEM_FALLBACK = "set_system_fallback"
    CREATE = "create"
    UPDATE_METADATA = "update_metadata"
    REPLACE = "replace"
    RESTORE = "restore"
    ARCHIVE = "archive"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class TemplateAuthorization:
    """Audit context that must accompany a later sensitive mutation."""

    actor_id: UUID
    owner_id: UUID
    template_id: UUID
    operation: TemplateOperation
    administrator_intervention: bool


class TemplateService:
    """Application service without T15 content or version mutation behavior."""

    def __init__(  # noqa: PLR0913 - explicit application ports
        self,
        *,
        catalog: TemplateCatalogRepository,
        selections: TemplateSelectionRepository,
        objects: ObjectStore | None = None,
        validate_content: Callable[[bytes], str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._catalog = catalog
        self._selections = selections
        self._objects = objects
        self._validate_content = validate_content
        self._clock = clock
        self._new_id = new_id

    def create(self, actor: User, request: TemplateCreate) -> TemplateIdentity:
        """Create an identity whose immutable owner is always the authenticated actor."""
        template = TemplateIdentity(
            id=request.id,
            owner_id=actor.id,
            name=request.name,
            description=request.description,
            status=request.status,
        )
        self._catalog.add(template)
        return template

    def create_versioned(
        self, actor: User, request: TemplateCreate, content: bytes
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        """Validate and atomically publish the initial immutable content version."""
        objects, validator = self._runtime()
        digest = validator(content)
        version_id = self._new_id()
        template = TemplateIdentity(
            request.id,
            actor.id,
            request.name,
            request.description,
            TemplateStatus.ACTIVE,
            1,
            version_id,
        )
        version = self._new_version(
            actor, template, version_id, content, digest, number=1
        )
        key = self._key(version)
        self._put(objects, key, content)
        try:
            persisted = self._catalog.create_versioned(
                template,
                version,
                self._audit(actor, template, TemplateOperation.CREATE, version.id),
            )
        except BaseException:
            self._delete(objects, key)
            raise
        return persisted, version

    def update_metadata(
        self,
        actor: User,
        template_id: UUID,
        *,
        expected_revision: int,
        name: str,
        description: str,
    ) -> TemplateIdentity:
        authorization = self.authorize_mutation(actor, template_id)
        candidate = TemplateIdentity(
            template_id,
            authorization.owner_id,
            name,
            description,
            TemplateStatus.ACTIVE,
        )
        return self._catalog.update_metadata(
            template_id,
            expected_revision=expected_revision,
            name=candidate.name,
            description=candidate.description,
            audit=self._audit_from_authorization(
                authorization, TemplateOperation.UPDATE_METADATA
            ),
        )

    def replace(
        self,
        actor: User,
        template_id: UUID,
        *,
        expected_revision: int,
        content: bytes,
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        objects, validator = self._runtime()
        template = self.get_visible(actor, template_id)
        authorization = self.authorize_mutation(actor, template_id)
        if template.status is not TemplateStatus.ACTIVE:
            raise TemplateConflictError
        digest = validator(content)
        versions = self._catalog.list_versions(template_id)
        version = self._new_version(
            actor,
            template,
            self._new_id(),
            content,
            digest,
            number=max((item.number for item in versions), default=0) + 1,
        )
        return self._publish(
            objects,
            template,
            version,
            content,
            expected_revision,
            self._audit_from_authorization(
                authorization, TemplateOperation.REPLACE, version.id
            ),
        )

    def restore(
        self,
        actor: User,
        template_id: UUID,
        source_version_id: UUID,
        *,
        expected_revision: int,
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        objects, _validator = self._runtime()
        template = self.get_visible(actor, template_id)
        authorization = self.authorize_mutation(actor, template_id)
        source = self._catalog.get_version(template_id, source_version_id)
        if source is None or template.status is not TemplateStatus.ACTIVE:
            raise TemplateUnavailableError
        content = self._get(objects, self._key(source))
        versions = self._catalog.list_versions(template_id)
        version = replace(
            source,
            id=self._new_id(),
            number=max(item.number for item in versions) + 1,
            object_owner_id=template.owner_id,
            created_at=self._clock(),
            created_by=actor.id,
            restored_from_version_id=source.id,
        )
        return self._publish(
            objects,
            template,
            version,
            content,
            expected_revision,
            self._audit_from_authorization(
                authorization, TemplateOperation.RESTORE, version.id
            ),
        )

    def archive(
        self, actor: User, template_id: UUID, *, expected_revision: int
    ) -> TemplateIdentity:
        authorization = self.authorize_mutation(actor, template_id)
        return self._catalog.set_status(
            template_id,
            expected_revision=expected_revision,
            status=TemplateStatus.ARCHIVED.value,
            audit=self._audit_from_authorization(
                authorization, TemplateOperation.ARCHIVE
            ),
        )

    def delete(self, actor: User, template_id: UUID, *, expected_revision: int) -> None:
        objects, _validator = self._runtime()
        authorization = self.authorize_mutation(actor, template_id)
        versions = self._catalog.delete_guarded(
            template_id,
            expected_revision=expected_revision,
            audit=self._audit_from_authorization(
                authorization, TemplateOperation.DELETE
            ),
        )
        for version in versions:
            self._delete(objects, self._key(version))

    def list_versions(
        self, actor: User, template_id: UUID
    ) -> tuple[TemplateVersion, ...]:
        self.get_visible(actor, template_id)
        return self._catalog.list_versions(template_id)

    def download(
        self, actor: User, template_id: UUID, version_id: UUID | None = None
    ) -> tuple[TemplateIdentity, TemplateVersion, bytes]:
        objects, _validator = self._runtime()
        template = self.get_visible(actor, template_id)
        selected_id = version_id or template.current_version_id
        if selected_id is None:
            raise TemplateUnavailableError
        version = self._catalog.get_version(template_id, selected_id)
        if version is None:
            raise TemplateUnavailableError
        return template, version, self._get(objects, self._key(version))

    def resolve_frozen_version(
        self, template_id: UUID, version_id: UUID
    ) -> tuple[TemplateVersion, bytes]:
        """Give a processor the exact immutable version frozen on its job."""
        objects, _validator = self._runtime()
        version = self._catalog.get_version(template_id, version_id)
        if version is None:
            raise TemplateUnavailableError
        return version, self._get(objects, self._key(version))

    def search(self, actor: User, query: TemplateSearch) -> TemplatePage:
        return self._catalog.search(
            query,
            viewer_id=actor.id,
            viewer_is_admin=actor.role is Role.ADMIN,
        )

    def get_visible(self, actor: User, template_id: UUID) -> TemplateIdentity:
        template = self._catalog.get(template_id)
        if template is None or not self._is_visible(actor, template):
            raise TemplateUnavailableError
        return template

    def authorize_mutation(
        self, actor: User, template_id: UUID
    ) -> TemplateAuthorization:
        template = self._catalog.get(template_id)
        if template is None:
            raise TemplateUnavailableError
        AuthorizationService.require_owner_or_admin(actor, template.owner_id)
        return self._authorization(actor, template, TemplateOperation.MUTATE)

    def set_preferred(self, actor: User, template_id: UUID) -> None:
        self._selections.set_preferred(actor.id, template_id)

    def clear_preferred(self, actor: User) -> None:
        self._selections.clear_preferred(actor.id)

    def set_system_fallback(
        self, actor: User, template_id: UUID
    ) -> TemplateAuthorization:
        AuthorizationService.require_admin(actor)
        template = self._catalog.get(template_id)
        if template is None or template.status is not TemplateStatus.ACTIVE:
            raise TemplateUnavailableError
        authorization = self._authorization(
            actor, template, TemplateOperation.SET_SYSTEM_FALLBACK
        )
        if self._objects is None:
            self._selections.set_system_fallback(template_id)
        else:
            self._selections.set_system_fallback_audited(
                template_id,
                self._audit_from_authorization(
                    authorization, TemplateOperation.SET_SYSTEM_FALLBACK
                ),
            )
        return authorization

    def resolve(self, actor: User) -> TemplateIdentity | None:
        return self._selections.resolve(actor.id)

    @staticmethod
    def _is_visible(actor: User, template: TemplateIdentity) -> bool:
        return (
            template.status is TemplateStatus.ACTIVE
            or template.owner_id == actor.id
            or actor.role is Role.ADMIN
        )

    @staticmethod
    def _authorization(
        actor: User,
        template: TemplateIdentity,
        operation: TemplateOperation,
    ) -> TemplateAuthorization:
        return TemplateAuthorization(
            actor_id=actor.id,
            owner_id=template.owner_id,
            template_id=template.id,
            operation=operation,
            administrator_intervention=(
                actor.role is Role.ADMIN
                and (
                    actor.id != template.owner_id
                    or operation is TemplateOperation.SET_SYSTEM_FALLBACK
                )
            ),
        )

    def _runtime(self) -> tuple[ObjectStore, Callable[[bytes], str]]:
        if self._objects is None or self._validate_content is None:
            raise RuntimeError("Template version runtime is not configured")
        return self._objects, self._validate_content

    def _new_version(  # noqa: PLR0913 - immutable version fields are explicit
        self,
        actor: User,
        template: TemplateIdentity,
        version_id: UUID,
        content: bytes,
        digest: str,
        *,
        number: int,
    ) -> TemplateVersion:
        return TemplateVersion(
            version_id,
            template.id,
            number,
            template.owner_id,
            digest,
            len(content),
            self._clock(),
            actor.id,
        )

    @staticmethod
    def _key(version: TemplateVersion) -> ObjectKey:
        return ObjectKey(
            ObjectScope.TEMPLATE_VERSION, version.object_owner_id, version.id
        )

    def _publish(  # noqa: PLR0913, PLR0917 - compensation boundary is explicit
        self,
        objects: ObjectStore,
        template: TemplateIdentity,
        version: TemplateVersion,
        content: bytes,
        expected_revision: int,
        audit: TemplateAuditRecord,
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        key = self._key(version)
        self._put(objects, key, content)
        try:
            updated = self._catalog.publish_version(
                template.id,
                expected_revision=expected_revision,
                version=version,
                audit=audit,
            )
        except BaseException:
            self._delete(objects, key)
            raise
        return updated, version

    def _audit(
        self,
        actor: User,
        template: TemplateIdentity,
        operation: TemplateOperation,
        version_id: UUID | None = None,
    ) -> TemplateAuditRecord:
        authorization = self._authorization(actor, template, operation)
        return self._audit_from_authorization(authorization, operation, version_id)

    def _audit_from_authorization(
        self,
        authorization: TemplateAuthorization,
        operation: TemplateOperation,
        version_id: UUID | None = None,
    ) -> TemplateAuditRecord:
        return TemplateAuditRecord(
            self._new_id(),
            authorization.actor_id,
            authorization.owner_id,
            authorization.template_id,
            operation.value,
            version_id,
            authorization.administrator_intervention,
            self._clock(),
        )

    @staticmethod
    def _put(objects: ObjectStore, key: ObjectKey, content: bytes) -> None:
        try:
            objects.put(key, content)
        except ObjectStoreError:
            raise TemplateStorageError from None

    @staticmethod
    def _get(objects: ObjectStore, key: ObjectKey) -> bytes:
        try:
            return objects.get(key)
        except ObjectNotFoundError, ObjectStoreError:
            raise TemplateStorageError from None

    @staticmethod
    def _delete(objects: ObjectStore, key: ObjectKey) -> None:
        try:
            objects.delete(key)
        except ObjectStoreError:
            raise TemplateStorageError from None
