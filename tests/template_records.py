"""Explicit published template records for queue boundary tests."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from md_converter.persistence.schema import TemplateRow, TemplateVersionRow


def publish_template_pair(
    engine: Engine, owner_id: UUID, template_id: UUID, version_id: UUID
) -> None:
    """Create one active current pair without crossing the T10 engine boundary."""
    with Session(engine) as database, database.begin():
        database.add(
            TemplateRow(
                id=str(template_id),
                owner_id=str(owner_id),
                name="Queue fixture",
                normalized_name="queue fixture",
                description="Published test pair",
                normalized_description="published test pair",
                status="active",
                revision=1,
                current_version_id=None,
                publication_state="published",
            )
        )
        database.add(
            TemplateVersionRow(
                id=str(version_id),
                template_id=str(template_id),
                version_number=1,
                object_owner_id=str(owner_id),
                sha256="0" * 64,
                size=1,
                created_at=datetime.now(UTC),
                created_by=str(owner_id),
                restored_from_version_id=None,
                declared_fonts='["Calibri"]',
                resolved_fonts='[["Calibri","Carlito"]]',
                validation_trace='["static_ooxml"]',
                publication_state="published",
            )
        )
        database.flush()
        database.execute(
            update(TemplateRow)
            .where(TemplateRow.id == str(template_id))
            .values(current_version_id=str(version_id))
        )
