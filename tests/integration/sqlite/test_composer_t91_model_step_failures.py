"""Model-step failures keep dispatch and human decisions behind durable fences."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from markweave.composer.author_knowledge import AuthorKnowledgeLimits
from markweave.composer.revisions import ComposerConflictError, ComposerNotFoundError
from markweave.persistence.composer import SqlComposerModelStepRepository
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.schema import ComposerModelStepRow
from tests.integration.sqlite.test_composer_model_steps import (
    _ENDPOINT,
    _MODEL,
    _PAYLOAD,
    _draft,
    _setup,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def test_model_step_rejects_invalid_admission_and_missing_identities(
    tmp_path: Path,
) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        draft = _draft(drafts, owner)
        with pytest.raises(ComposerNotFoundError):
            steps.get_model_step(owner, draft.id, uuid4())
        with pytest.raises(ComposerNotFoundError):
            steps.get_question(owner, draft.id, uuid4())
        invalid_cases: tuple[
            tuple[str, tuple[tuple[UUID, int], ...], str | None], ...
        ] = (
            ("", (), None),
            ("admission", ((uuid4(), 1),), None),
            (
                "admission",
                ((owner, 1), (owner, 1)),
                hashlib.sha256(b"preview").hexdigest(),
            ),
        )
        for key, refs, digest in invalid_cases:
            with pytest.raises(ValueError, match="admission"):
                steps.start_model_step(
                    owner,
                    draft.id,
                    connection_id=connection_id,
                    approved_endpoint=_ENDPOINT,
                    approved_model=_MODEL,
                    if_match=draft.etag,
                    idempotency_key=key,
                    payload_digest=_PAYLOAD,
                    max_active=2,
                    lease=timedelta(minutes=5),
                    author_refs=refs,
                    author_preview_digest=digest,
                )
        with pytest.raises(ValueError, match="error code"):
            steps.fail_model_step(owner, draft.id, uuid4(), error_code="INVALID CODE")
        with pytest.raises(ValueError, match="limit"):
            steps.recover_stale_model_steps(stale_before=datetime.now(UTC), limit=0)
    finally:
        engine.dispose()


def test_dispatch_fails_if_author_directory_or_connection_disappears(
    tmp_path: Path,
) -> None:
    engine, drafts, _steps, owner, author_owner, connection_id = _setup(tmp_path)
    authors = SqlAuthorKnowledgeRepository(
        engine, AuthorKnowledgeLimits(10, 100, 100, 100, 100)
    )
    steps = SqlComposerModelStepRepository(engine, authors=authors)
    try:
        draft = _draft(drafts, owner)
        author = authors.create(
            author_owner,
            "Ada",
            json.dumps({"role": {"value": "Reviewer", "provenance": "supplied"}}),
        )
        shared = authors.grant(author_owner, author.id, owner, if_match=author.etag)
        step, created = steps.start_model_step(
            owner,
            draft.id,
            connection_id=connection_id,
            approved_endpoint=_ENDPOINT,
            approved_model=_MODEL,
            if_match=draft.etag,
            idempotency_key="author-dispatch",
            payload_digest=_PAYLOAD,
            max_active=2,
            lease=timedelta(minutes=5),
            author_refs=((author.id, shared.version),),
            author_preview_digest=hashlib.sha256(b"reviewed preview").hexdigest(),
        )
        assert created
        without_authors = SqlComposerModelStepRepository(engine)
        with pytest.raises(ComposerConflictError, match="directory is unavailable"):
            without_authors.authorize_before_dispatch(owner, draft.id, step.id)
        with Session(engine) as database, database.begin():
            row = database.get(ComposerModelStepRow, str(step.id))
            assert row is not None
            row.connection_id = str(uuid4())
        with pytest.raises(ComposerConflictError, match="Connection changed"):
            steps.authorize_before_dispatch(owner, draft.id, step.id)
    finally:
        engine.dispose()


def test_question_answer_and_replayed_result_reject_changed_state(
    tmp_path: Path,
) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        draft = _draft(drafts, owner)
        step, created = steps.start_model_step(
            owner,
            draft.id,
            connection_id=connection_id,
            approved_endpoint=_ENDPOINT,
            approved_model=_MODEL,
            if_match=draft.etag,
            idempotency_key="question",
            payload_digest=_PAYLOAD,
            max_active=2,
            lease=timedelta(minutes=5),
            intent="question",
        )
        assert created
        question = steps.finish_model_step(
            owner,
            draft.id,
            step.id,
            proposed_value="Who is the audience?",
            provenance=f"model-step:{step.id}",
        )
        with pytest.raises(ComposerConflictError, match="result changed"):
            steps.finish_model_step(
                owner,
                draft.id,
                step.id,
                proposed_value="A different question?",
                provenance=f"model-step:{step.id}",
            )
        current = drafts.get_draft(owner, draft.id)
        edited = drafts.save_draft(
            owner,
            draft.id,
            if_match=current.etag,
            title=current.title,
            content="Human change after question",
        )
        with pytest.raises(ComposerConflictError, match="Question changed"):
            steps.answer_question(
                owner,
                draft.id,
                question.id,
                content="Researchers",
                if_match=edited.etag,
                idempotency_key="late-answer",
            )
    finally:
        engine.dispose()
