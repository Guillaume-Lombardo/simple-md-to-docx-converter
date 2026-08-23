"""Template visibility, selection, and future-mutation authorization services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from md_converter.auth.models import Role, User
from md_converter.auth.service import AuthorizationService
from md_converter.templates.errors import TemplateUnavailableError
from md_converter.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
)
from md_converter.templates.ports import (
    TemplateCatalogRepository,
    TemplateSelectionRepository,
)


class TemplateOperation(StrEnum):
    """Sensitive operations T15 can persist through its audit adapter."""

    MUTATE = "mutate"
    SET_SYSTEM_FALLBACK = "set_system_fallback"


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

    def __init__(
        self,
        *,
        catalog: TemplateCatalogRepository,
        selections: TemplateSelectionRepository,
    ) -> None:
        self._catalog = catalog
        self._selections = selections

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
        self._selections.set_system_fallback(template_id)
        return self._authorization(
            actor, template, TemplateOperation.SET_SYSTEM_FALLBACK
        )

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
