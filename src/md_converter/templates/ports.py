"""Persistence ports for template identity and user selection."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateVersion,
)


class TemplateCatalogRepository(Protocol):
    """Immutable-owner template identity and visibility queries."""

    def add(self, template: TemplateIdentity) -> None: ...

    def get(self, template_id: UUID) -> TemplateIdentity | None: ...

    def search(
        self,
        query: TemplateSearch,
        *,
        viewer_id: UUID,
        viewer_is_admin: bool,
    ) -> TemplatePage: ...

    def create_versioned(
        self,
        template: TemplateIdentity,
        version: TemplateVersion,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity: ...

    def reserve_create(
        self, template: TemplateIdentity, version: TemplateVersion
    ) -> None: ...

    def reserve_version(
        self, template_id: UUID, *, expected_revision: int, version: TemplateVersion
    ) -> TemplateVersion: ...

    def finalize_version(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        version_id: UUID,
        publication_token: UUID,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity: ...

    def abort_pending(
        self, template_id: UUID, version_id: UUID, publication_token: UUID
    ) -> bool: ...

    def claim_stale_pending(
        self,
        *,
        stale_before: datetime,
        lease_expires_at: datetime,
        publication_token: UUID,
    ) -> tuple[TemplateVersion, ...]: ...

    def release_pending_claim(
        self,
        template_id: UUID,
        version_id: UUID,
        publication_token: UUID,
        *,
        retry_at: datetime,
    ) -> bool: ...

    def pending_deletions(
        self,
    ) -> tuple[tuple[UUID, tuple[TemplateVersion, ...]], ...]: ...

    def begin_delete(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        audit: TemplateAuditRecord,
    ) -> tuple[TemplateVersion, ...]: ...

    def finalize_delete(self, template_id: UUID) -> None: ...

    def update_metadata(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        name: str,
        description: str,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity: ...

    def publish_version(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        version: TemplateVersion,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity: ...

    def set_status(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        status: str,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity: ...

    def delete_guarded(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        audit: TemplateAuditRecord,
    ) -> tuple[TemplateVersion, ...]: ...

    def get_version(
        self, template_id: UUID, version_id: UUID
    ) -> TemplateVersion | None: ...

    def list_versions(self, template_id: UUID) -> tuple[TemplateVersion, ...]: ...


class TemplateSelectionRepository(Protocol):
    """Transactional preferred-template and system-fallback selection."""

    def set_preferred(self, user_id: UUID, template_id: UUID) -> None: ...

    def set_preferred_audited(
        self, user_id: UUID, template_id: UUID, audit: TemplateAuditRecord
    ) -> None: ...

    def clear_preferred(self, user_id: UUID) -> None: ...

    def clear_preferred_audited(
        self, user_id: UUID, audit: TemplateAuditRecord
    ) -> None: ...

    def preferred_id(self, user_id: UUID) -> UUID | None: ...

    def set_system_fallback(self, template_id: UUID) -> None: ...

    def set_system_fallback_audited(
        self, template_id: UUID, audit: TemplateAuditRecord
    ) -> None: ...

    def system_fallback_id(self) -> UUID | None: ...

    def resolve(self, user_id: UUID) -> TemplateIdentity | None: ...
