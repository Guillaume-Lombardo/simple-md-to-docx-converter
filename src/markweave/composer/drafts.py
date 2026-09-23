"""Owner-scoped Composer draft, conversation, and proposal contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from markweave.composer.revisions import SourceReference


class ProposalState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ComposerDraft:
    id: UUID
    owner_id: UUID
    title: str
    source: SourceReference
    content: str
    version: int
    current_revision_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


@dataclass(frozen=True, slots=True)
class ComposerMessage:
    id: UUID
    draft_id: UUID
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ComposerProposal:
    id: UUID
    draft_id: UUID
    base_version: int
    state: ProposalState
    proposed_value: str
    decided_value: str | None
    provenance: str
    created_at: datetime
    decided_at: datetime | None
    decided_by: UUID | None
