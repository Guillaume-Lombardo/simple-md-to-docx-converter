"""Typed, durable assistant questions with no duplicated answer content."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from markweave.persistence.composer.common import utc
from markweave.persistence.schema import ComposerQuestionRow

MAX_QUESTION_CHARACTERS = 500
_QUESTION_OPENERS = frozenset(
    {
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "can",
        "could",
        "would",
        "should",
        "will",
        "do",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "may",
        "might",
        "must",
    }
)


def validated_question(content: str) -> str:
    """Accept only one plain question, never a proposal-shaped completion."""

    question = content.strip()
    if (
        not question
        or len(question) > MAX_QUESTION_CHARACTERS
        or "\n" in question
        or question.count("?") != 1
        or not question.endswith("?")
        or question.startswith(("#", "-", "*", ">", "`"))
        or question.split(maxsplit=1)[0].lower() not in _QUESTION_OPENERS
    ):
        raise ValueError("Provider question is invalid")
    return question


@dataclass(frozen=True, slots=True)
class ComposerQuestion:
    id: UUID
    draft_id: UUID
    model_step_id: UUID
    base_version: int
    state: str
    text: str
    answer_message_id: UUID | None
    answer_content: str | None
    created_at: datetime
    answered_at: datetime | None


def question_from_row(
    row: ComposerQuestionRow, *, answer_content: str | None
) -> ComposerQuestion:
    return ComposerQuestion(
        id=UUID(row.id),
        draft_id=UUID(row.draft_id),
        model_step_id=UUID(row.model_step_id),
        base_version=row.base_version,
        state=row.state,
        text=row.text,
        answer_message_id=UUID(row.answer_message_id)
        if row.answer_message_id
        else None,
        answer_content=answer_content,
        created_at=utc(row.created_at),
        answered_at=utc(row.answered_at) if row.answered_at else None,
    )
