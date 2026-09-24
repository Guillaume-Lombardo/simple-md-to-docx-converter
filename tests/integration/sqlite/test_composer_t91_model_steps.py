"""Selected author facts remain fenced through model egress and publication."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from markweave.composer.author_knowledge import AuthorKnowledgeLimits
from markweave.composer.revisions import ComposerConflictError
from markweave.persistence.composer import SqlComposerModelStepRepository
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.questions import ComposerQuestion
from tests.integration.sqlite.test_composer_model_steps import (
    _ENDPOINT,
    _MODEL,
    _PAYLOAD,
    _draft,
    _setup,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def test_revoke_after_admission_denies_dispatch_and_result_publication(
    tmp_path: Path,
) -> None:
    engine, drafts, _unused_steps, owner, other, connection_id = _setup(tmp_path)
    authors = SqlAuthorKnowledgeRepository(
        engine,
        AuthorKnowledgeLimits(10, 100, 100, 100, 100),
    )
    steps = SqlComposerModelStepRepository(engine, authors=authors)
    try:
        draft = _draft(drafts, owner)
        entry = authors.create(
            other,
            "Ada",
            json.dumps({"affiliation": {"value": "Private", "provenance": "supplied"}}),
        )
        granted = authors.grant(other, entry.id, owner, if_match=entry.etag)
        refs = ((entry.id, granted.version),)
        step, created = steps.start_model_step(
            owner,
            draft.id,
            connection_id=connection_id,
            approved_endpoint=_ENDPOINT,
            approved_model=_MODEL,
            if_match=draft.etag,
            idempotency_key="author-step",
            payload_digest=_PAYLOAD,
            max_active=2,
            lease=timedelta(minutes=5),
            author_refs=refs,
            author_preview_digest=hashlib.sha256(b"reviewed prompt").hexdigest(),
        )
        assert created
        steps.authorize_before_dispatch(owner, draft.id, step.id)
        revoked = authors.revoke(other, entry.id, owner, if_match=granted.etag)
        with pytest.raises(ComposerConflictError, match="Author access"):
            steps.authorize_before_dispatch(owner, draft.id, step.id)
        with pytest.raises(ComposerConflictError, match="Author access"):
            steps.finish_model_step(
                owner,
                draft.id,
                step.id,
                proposed_value="proposal",
                provenance=f"model-step:{step.id}",
            )
        authors.grant(other, entry.id, owner, if_match=revoked.etag)
        with pytest.raises(ComposerConflictError, match="Author access"):
            steps.authorize_before_dispatch(owner, draft.id, step.id)
    finally:
        engine.dispose()


def test_answer_resume_inherits_original_author_grant_and_version(
    tmp_path: Path,
) -> None:
    engine, drafts, _unused_steps, owner, other, connection_id = _setup(tmp_path)
    authors = SqlAuthorKnowledgeRepository(
        engine, AuthorKnowledgeLimits(10, 100, 100, 100, 100)
    )
    steps = SqlComposerModelStepRepository(engine, authors=authors)
    try:
        draft = _draft(drafts, owner)
        entry = authors.create(other, "Ada", "{}")
        shared = authors.grant(other, entry.id, owner, if_match=entry.etag)
        refs = ((entry.id, shared.version),)
        digest = hashlib.sha256(b"reviewed author prompt").hexdigest()
        original, created = steps.start_model_step(
            owner,
            draft.id,
            connection_id=connection_id,
            approved_endpoint=_ENDPOINT,
            approved_model=_MODEL,
            if_match=draft.etag,
            idempotency_key="question-with-author",
            payload_digest=_PAYLOAD,
            max_active=2,
            lease=timedelta(minutes=5),
            intent="question",
            author_refs=refs,
            author_preview_digest=digest,
        )
        assert created
        question = steps.finish_model_step(
            owner,
            draft.id,
            original.id,
            proposed_value="Which audience should Ada address?",
            provenance=f"model-step:{original.id}",
        )
        assert isinstance(question, ComposerQuestion)
        assert question.source_author_ids == (entry.id,)
        assert steps.get_question(owner, draft.id, question.id).source_author_ids == (
            entry.id,
        )
        steps.answer_question(
            owner,
            draft.id,
            question.id,
            content="Researchers",
            if_match='"2"',
            idempotency_key="answer",
        )
        with pytest.raises(ComposerConflictError, match="Author access"):
            steps.start_model_step(
                owner,
                draft.id,
                connection_id=connection_id,
                approved_endpoint=_ENDPOINT,
                approved_model=_MODEL,
                if_match='"3"',
                idempotency_key="resume",
                payload_digest=_PAYLOAD,
                max_active=2,
                lease=timedelta(minutes=5),
                answered_question_id=question.id,
            )
        resumed, created = steps.start_model_step(
            owner,
            draft.id,
            connection_id=connection_id,
            approved_endpoint=_ENDPOINT,
            approved_model=_MODEL,
            if_match='"3"',
            idempotency_key="resume",
            payload_digest=_PAYLOAD,
            max_active=2,
            lease=timedelta(minutes=5),
            answered_question_id=question.id,
            author_refs=refs,
            author_preview_digest=digest,
        )
        assert created
        steps.authorize_before_dispatch(owner, draft.id, resumed.id)
        authors.revoke(other, entry.id, owner, if_match=shared.etag)
        with pytest.raises(ComposerConflictError, match="Author access"):
            steps.authorize_before_dispatch(owner, draft.id, resumed.id)
        with pytest.raises(ComposerConflictError, match="Author access"):
            steps.finish_model_step(
                owner,
                draft.id,
                resumed.id,
                proposed_value="Suggestion",
                provenance=f"model-step:{resumed.id}",
            )
    finally:
        engine.dispose()
