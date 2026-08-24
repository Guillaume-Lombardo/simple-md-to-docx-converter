"""Persistence ports for template identity and user selection."""

from __future__ import annotations

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

    def clear_preferred(self, user_id: UUID) -> None: ...

    def preferred_id(self, user_id: UUID) -> UUID | None: ...

    def set_system_fallback(self, template_id: UUID) -> None: ...

    def set_system_fallback_audited(
        self, template_id: UUID, audit: TemplateAuditRecord
    ) -> None: ...

    def system_fallback_id(self) -> UUID | None: ...

    def resolve(self, user_id: UUID) -> TemplateIdentity | None: ...
