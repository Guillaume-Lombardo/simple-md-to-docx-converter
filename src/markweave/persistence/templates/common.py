"""Shared SQL template mapping and dialect primitives."""

from __future__ import annotations

import json
from datetime import UTC
from uuid import UUID

from sqlalchemy import Engine

from markweave.persistence.schema import (
    TemplateRow,
    TemplateVersionRow,
)
from markweave.templates.models import (
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)

SYSTEM_TEMPLATE_SELECTION_ID = 1


def _template(row: TemplateRow) -> TemplateIdentity:
    return TemplateIdentity(
        id=UUID(row.id),
        owner_id=UUID(row.owner_id),
        name=row.name,
        description=row.description,
        status=TemplateStatus(row.status),
        revision=row.revision,
        current_version_id=(
            UUID(row.current_version_id) if row.current_version_id else None
        ),
    )


def _version(row: TemplateVersionRow) -> TemplateVersion:
    created_at = (
        row.created_at.replace(tzinfo=UTC)
        if row.created_at.tzinfo is None
        else row.created_at.astimezone(UTC)
    )
    return TemplateVersion(
        id=UUID(row.id),
        template_id=UUID(row.template_id),
        number=row.version_number,
        object_owner_id=UUID(row.object_owner_id),
        sha256=row.sha256,
        size=row.size,
        created_at=created_at,
        created_by=UUID(row.created_by),
        restored_from_version_id=(
            UUID(row.restored_from_version_id) if row.restored_from_version_id else None
        ),
        declared_fonts=tuple(json.loads(row.declared_fonts)),
        resolved_fonts=tuple(tuple(item) for item in json.loads(row.resolved_fonts)),
        validation_trace=tuple(json.loads(row.validation_trace)),
        publication_state=TemplatePublicationState(row.publication_state),
        publication_token=(
            UUID(row.publication_token) if row.publication_token else None
        ),
        publication_lease_expires_at=(
            row.publication_lease_expires_at.replace(tzinfo=UTC)
            if row.publication_lease_expires_at is not None
            and row.publication_lease_expires_at.tzinfo is None
            else row.publication_lease_expires_at
        ),
    )


def _version_row(version: TemplateVersion) -> TemplateVersionRow:
    return TemplateVersionRow(
        id=str(version.id),
        template_id=str(version.template_id),
        version_number=version.number,
        object_owner_id=str(version.object_owner_id),
        sha256=version.sha256,
        size=version.size,
        created_at=version.created_at,
        created_by=str(version.created_by),
        restored_from_version_id=(
            str(version.restored_from_version_id)
            if version.restored_from_version_id
            else None
        ),
        declared_fonts=json.dumps(version.declared_fonts, separators=(",", ":")),
        resolved_fonts=json.dumps(version.resolved_fonts, separators=(",", ":")),
        validation_trace=json.dumps(version.validation_trace, separators=(",", ":")),
        publication_state=version.publication_state.value,
        publication_token=(
            str(version.publication_token) if version.publication_token else None
        ),
        publication_lease_expires_at=version.publication_lease_expires_at,
    )


class _SqlTemplateStore:
    """Shared SQL engine for template stores."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
