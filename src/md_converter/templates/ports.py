"""Persistence ports for template identity and user selection."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from md_converter.templates.models import TemplateIdentity, TemplatePage, TemplateSearch


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


class TemplateSelectionRepository(Protocol):
    """Transactional preferred-template and system-fallback selection."""

    def set_preferred(self, user_id: UUID, template_id: UUID) -> None: ...

    def clear_preferred(self, user_id: UUID) -> None: ...

    def preferred_id(self, user_id: UUID) -> UUID | None: ...

    def set_system_fallback(self, template_id: UUID) -> None: ...

    def system_fallback_id(self) -> UUID | None: ...

    def resolve(self, user_id: UUID) -> TemplateIdentity | None: ...
