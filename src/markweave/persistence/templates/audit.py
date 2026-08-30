"""Immutable template audit row persistence mapping."""

from __future__ import annotations

from markweave.persistence.schema import (
    TemplateAuditRow,
)
from markweave.templates.models import (
    TemplateAuditRecord,
)


def _audit_row(audit: TemplateAuditRecord) -> TemplateAuditRow:
    """Map a content-free domain audit record into its immutable SQL row."""

    return TemplateAuditRow(
        id=str(audit.id),
        actor_id=str(audit.actor_id),
        owner_id=str(audit.owner_id),
        template_id=str(audit.template_id),
        operation=audit.operation,
        version_id=str(audit.version_id) if audit.version_id else None,
        administrator_intervention=audit.administrator_intervention,
        created_at=audit.created_at,
    )
