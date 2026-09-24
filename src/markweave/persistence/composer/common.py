"""Shared Composer SQL mapping and owner-scoped lookups."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from markweave.composer.drafts import (
    ComposerDraft,
    ComposerMessage,
    ComposerProposal,
    ProposalState,
)
from markweave.composer.revisions import ComposerNotFoundError, SourceReference
from markweave.persistence.schema import (
    ComposerDraftRow,
    ComposerMessageRow,
    ComposerProposalRow,
)

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
PageOrder = Literal["asc", "desc"]


def validate_page(limit: int, offset: int) -> None:
    """Reject unbounded or malformed owner history reads before querying SQL."""

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_PAGE_LIMIT
    ):
        raise ValueError("Composer page limit must be between 1 and 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("Composer page offset must be non-negative")


def validate_page_order(order: PageOrder) -> None:
    """Allow only the two explicit SQL history orders."""

    if order not in ("asc", "desc"):
        raise ValueError("Composer page order must be asc or desc")


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def source_json(source: SourceReference) -> str:
    return json.dumps(
        {
            "kind": source.kind,
            "object_id": str(source.object_id),
            "owner_id": str(source.owner_id),
            "sha256": source.sha256,
            "scan_receipt": source.scan_receipt,
            "media_type": source.media_type,
            "origin_job_id": str(source.origin_job_id)
            if source.origin_job_id
            else None,
            "origin_result_object_id": str(source.origin_result_object_id)
            if source.origin_result_object_id
            else None,
            "origin_result_sha256": source.origin_result_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def source_from_json(value: str) -> SourceReference:
    data = json.loads(value)
    return SourceReference(
        kind=data["kind"],
        object_id=UUID(data["object_id"]),
        owner_id=UUID(data["owner_id"]),
        sha256=data["sha256"],
        scan_receipt=data["scan_receipt"],
        media_type=data["media_type"],
        origin_job_id=UUID(data["origin_job_id"])
        if data.get("origin_job_id")
        else None,
        origin_result_object_id=(
            UUID(data["origin_result_object_id"])
            if data.get("origin_result_object_id")
            else None
        ),
        origin_result_sha256=data.get("origin_result_sha256"),
    )


def draft_from_row(row: ComposerDraftRow) -> ComposerDraft:
    return ComposerDraft(
        UUID(row.id),
        UUID(row.owner_id),
        row.title,
        source_from_json(row.source_reference),
        row.content,
        row.version,
        UUID(row.current_revision_id) if row.current_revision_id else None,
        utc(row.created_at),
        utc(row.updated_at),
    )


def message_from_row(row: ComposerMessageRow) -> ComposerMessage:
    return ComposerMessage(
        UUID(row.id), UUID(row.draft_id), row.role, row.content, utc(row.created_at)
    )


def proposal_from_row(row: ComposerProposalRow) -> ComposerProposal:
    return ComposerProposal(
        UUID(row.id),
        UUID(row.draft_id),
        row.base_version,
        ProposalState(row.state),
        row.proposed_value,
        row.decided_value,
        row.provenance,
        utc(row.created_at),
        utc(row.decided_at) if row.decided_at else None,
        UUID(row.decided_by) if row.decided_by else None,
    )


def owned_draft(
    database: Session, owner_id: UUID, draft_id: UUID, *, lock: bool = False
) -> ComposerDraftRow:
    statement = select(ComposerDraftRow).where(
        ComposerDraftRow.id == str(draft_id),
        ComposerDraftRow.owner_id == str(owner_id),
        ComposerDraftRow.state == "active",
    )
    if lock:
        statement = statement.with_for_update()
    row = database.scalar(statement)
    if row is None:
        raise ComposerNotFoundError("Composer draft does not exist")
    return row


class SqlComposerBase:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
