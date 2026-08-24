"""Template visibility, selection, and future-mutation authorization services."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
    TemplateIntegrityError,
    TemplateStorageError,
    TemplateUnavailableError,
)
from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateCreate,
    TemplateIdentity,
    TemplatePage,
    TemplatePublicationState,
    TemplateSearch,
    TemplateStatus,
    TemplateVersion,
)
from md_converter.templates.ports import (
    TemplateCatalogRepository,
    TemplateSelectionRepository,
)
from md_converter.templates.validation import TemplateFontDeclaration, ValidatedTemplate


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
    SET_PREFERRED = "set_preferred"
    CLEAR_PREFERRED = "clear_preferred"


@dataclass(frozen=True, slots=True)
class TemplateAuthorization:
    """Audit context that must accompany a later sensitive mutation."""

    actor_id: UUID
    owner_id: UUID
    template_id: UUID
    operation: TemplateOperation
    administrator_intervention: bool


@dataclass(frozen=True, slots=True)
class TemplateRecoveryPolicy:
    """Caller-owned publication lease duration assigned to T18 configuration."""

    pending_publication_stale_seconds: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.pending_publication_stale_seconds, bool)
            or self.pending_publication_stale_seconds <= 0
        ):
            raise ValueError("Template publication lease duration must be positive")


class TemplateService:
    """Application service without T15 content or version mutation behavior."""

    def __init__(  # noqa: PLR0913 - explicit application ports
        self,
        *,
        catalog: TemplateCatalogRepository,
        selections: TemplateSelectionRepository,
        objects: ObjectStore | None = None,
        validate_content: Callable[[bytes, TemplateFontDeclaration], ValidatedTemplate]
        | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
        new_token: Callable[[], UUID] = uuid4,
        recovery_policy: TemplateRecoveryPolicy | None = None,
    ) -> None:
        self._catalog = catalog
        self._selections = selections
        self._objects = objects
        self._validate_content = validate_content
        self._clock = clock
        self._new_id = new_id
        self._new_token = new_token
        self._recovery_policy = recovery_policy

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
        self,
        actor: User,
        request: TemplateCreate,
        content: bytes,
        expected_fonts: tuple[str, ...],
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        """Validate and atomically publish the initial immutable content version."""
        objects, validator = self._runtime()
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
        validated = validator(content, TemplateFontDeclaration(expected_fonts))
        version = self._new_version(
            actor,
            template,
            version_id,
            content,
            validated,
            number=1,
        )
        key = self._key(version)
        self._catalog.reserve_create(template, version)
        try:
            self._put(objects, key, content)
            persisted = self._catalog.finalize_version(
                template.id,
                expected_revision=1,
                version_id=version.id,
                publication_token=self._publication_token(version),
                audit=self._audit(
                    actor, template, TemplateOperation.CREATE, version.id
                ),
            )
        except BaseException:
            self._compensate_pending(objects, version)
            raise
        return persisted, replace(
            version,
            publication_state=TemplatePublicationState.PUBLISHED,
            publication_token=None,
            publication_lease_expires_at=None,
        )

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
        expected_fonts: tuple[str, ...],
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        objects, validator = self._runtime()
        template = self.get_visible(actor, template_id)
        authorization = self.authorize_mutation(actor, template_id)
        if template.status is not TemplateStatus.ACTIVE:
            raise TemplateConflictError
        validated = validator(content, TemplateFontDeclaration(expected_fonts))
        version = self._new_version(
            actor,
            template,
            self._new_id(),
            content,
            validated,
            number=1,
        )
        version = self._catalog.reserve_version(
            template_id, expected_revision=expected_revision, version=version
        )
        return self._publish_reserved(
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
        content = self._verified_get(objects, source)
        version = replace(
            source,
            id=self._new_id(),
            number=1,
            object_owner_id=template.owner_id,
            created_at=self._clock(),
            created_by=actor.id,
            restored_from_version_id=source.id,
            publication_state=TemplatePublicationState.PENDING,
            publication_token=self._new_token(),
            publication_lease_expires_at=self._clock()
            + timedelta(
                seconds=self._required_recovery_policy().pending_publication_stale_seconds
            ),
        )
        version = self._catalog.reserve_version(
            template_id, expected_revision=expected_revision, version=version
        )
        return self._publish_reserved(
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
        versions = self._catalog.begin_delete(
            template_id,
            expected_revision=expected_revision,
            audit=self._audit_from_authorization(
                authorization, TemplateOperation.DELETE
            ),
        )
        for version in versions:
            self._delete(objects, self._key(version))
        self._catalog.finalize_delete(template_id)

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
        return template, version, self._verified_get(objects, version)

    def resolve_frozen_version(
        self, template_id: UUID, version_id: UUID
    ) -> tuple[TemplateVersion, bytes]:
        """Give a processor the exact immutable version frozen on its job."""
        objects, _validator = self._runtime()
        version = self._catalog.get_version(template_id, version_id)
        if version is None:
            raise TemplateUnavailableError
        return version, self._verified_get(objects, version)

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
        template = self.get_visible(actor, template_id)
        if template.status is not TemplateStatus.ACTIVE:
            raise TemplateUnavailableError
        self._selections.set_preferred_audited(
            actor.id,
            template_id,
            self._audit(actor, template, TemplateOperation.SET_PREFERRED),
        )

    def clear_preferred(self, actor: User) -> None:
        self._selections.clear_preferred_audited(
            actor.id,
            TemplateAuditRecord(
                self._new_id(),
                actor.id,
                actor.id,
                UUID(int=0),
                TemplateOperation.CLEAR_PREFERRED.value,
                None,
                False,
                self._clock(),
            ),
        )

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

    def _runtime(
        self,
    ) -> tuple[
        ObjectStore,
        Callable[[bytes, TemplateFontDeclaration], ValidatedTemplate],
    ]:
        if self._objects is None or self._validate_content is None:
            raise RuntimeError("Template version runtime is not configured")
        return self._objects, self._validate_content

    def _required_recovery_policy(self) -> TemplateRecoveryPolicy:
        if self._recovery_policy is None:
            raise RuntimeError("Template recovery policy is not configured")
        return self._recovery_policy

    def _new_version(  # noqa: PLR0913 - immutable version fields are explicit
        self,
        actor: User,
        template: TemplateIdentity,
        version_id: UUID,
        content: bytes,
        validated: ValidatedTemplate,
        *,
        number: int,
    ) -> TemplateVersion:
        policy = self._required_recovery_policy()
        created_at = self._clock()
        return TemplateVersion(
            version_id,
            template.id,
            number,
            template.owner_id,
            validated.sha256,
            len(content),
            created_at,
            actor.id,
            None,
            validated.declared_fonts,
            validated.resolved_fonts,
            (
                "static_ooxml",
                "pandoc_blank_conversion",
                "libreoffice_open_save",
            ),
            TemplatePublicationState.PENDING,
            self._new_token(),
            created_at + timedelta(seconds=policy.pending_publication_stale_seconds),
        )

    @staticmethod
    def _key(version: TemplateVersion) -> ObjectKey:
        return ObjectKey(
            ObjectScope.TEMPLATE_VERSION, version.object_owner_id, version.id
        )

    def _publish_reserved(  # noqa: PLR0913, PLR0917
        self,
        objects: ObjectStore,
        template: TemplateIdentity,
        version: TemplateVersion,
        content: bytes,
        expected_revision: int,
        audit: TemplateAuditRecord,
    ) -> tuple[TemplateIdentity, TemplateVersion]:
        key = self._key(version)
        try:
            self._put(objects, key, content)
            updated = self._catalog.finalize_version(
                template.id,
                expected_revision=expected_revision,
                version_id=version.id,
                publication_token=self._publication_token(version),
                audit=audit,
            )
        except BaseException:
            self._compensate_pending(objects, version)
            raise
        return updated, replace(
            version,
            publication_state=TemplatePublicationState.PUBLISHED,
            publication_token=None,
            publication_lease_expires_at=None,
        )

    def reclaim_pending(self) -> int:
        """Claim and retry stale unpublished objects plus deletion tombstones."""
        objects, _validator = self._runtime()
        policy = self._required_recovery_policy()
        now = self._clock()
        token = self._new_token()
        reclaimed = 0
        for version in self._catalog.claim_stale_pending(
            stale_before=now,
            lease_expires_at=now
            + timedelta(seconds=policy.pending_publication_stale_seconds),
            publication_token=token,
        ):
            try:
                objects.delete(self._key(version))
            except ObjectStoreError:
                self._catalog.release_pending_claim(
                    version.template_id,
                    version.id,
                    token,
                    retry_at=now,
                )
                continue
            if self._catalog.abort_pending(version.template_id, version.id, token):
                reclaimed += 1
        for template_id, versions in self._catalog.pending_deletions():
            try:
                for version in versions:
                    objects.delete(self._key(version))
            except ObjectStoreError:
                continue
            self._catalog.finalize_delete(template_id)
            reclaimed += 1
        return reclaimed

    def _compensate_pending(
        self, objects: ObjectStore, version: TemplateVersion
    ) -> None:
        try:
            objects.delete(self._key(version))
        except ObjectStoreError:
            return
        self._catalog.abort_pending(
            version.template_id,
            version.id,
            self._publication_token(version),
        )

    @staticmethod
    def _publication_token(version: TemplateVersion) -> UUID:
        if version.publication_token is None:  # guarded by the domain model
            raise TemplateConflictError
        return version.publication_token

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

    def _verified_get(self, objects: ObjectStore, version: TemplateVersion) -> bytes:
        content = self._get(objects, self._key(version))
        if (
            len(content) != version.size
            or hashlib.sha256(content).hexdigest() != version.sha256
        ):
            raise TemplateIntegrityError from None
        return content

    @staticmethod
    def _delete(objects: ObjectStore, key: ObjectKey) -> None:
        try:
            objects.delete(key)
        except ObjectStoreError:
            raise TemplateStorageError from None
