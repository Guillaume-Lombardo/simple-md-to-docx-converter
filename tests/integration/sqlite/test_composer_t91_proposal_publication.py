"""Model-sourced author references remain authorized through revision publication."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import Session

from markweave.composer.author_knowledge import AuthorKnowledgeLimits
from markweave.composer.drafts import ProposalState
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    RevisionSnapshot,
)
from markweave.persistence.composer import (
    SqlComposerModelStepRepository,
    SqlComposerRepository,
)
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.schema import (
    ComposerModelStepRow,
    ComposerProposalRow,
    ComposerRevisionRow,
)
from markweave.storage import FilesystemObjectStore, ObjectKey
from tests.integration.sqlite.test_composer_model_steps import (
    _ENDPOINT,
    _MODEL,
    _PAYLOAD,
    _draft,
    _setup,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]
_VALUE = "# Reviewed proposal\n"


def _prepared(tmp_path: Path):
    engine, drafts, _unused_steps, owner, author_owner, connection_id = _setup(tmp_path)
    authors = SqlAuthorKnowledgeRepository(
        engine, AuthorKnowledgeLimits(10, 100, 100, 100, 100)
    )
    steps = SqlComposerModelStepRepository(engine, authors=authors)
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
        idempotency_key="model-with-author",
        payload_digest=_PAYLOAD,
        max_active=2,
        lease=timedelta(minutes=5),
        author_refs=((author.id, shared.version),),
        author_preview_digest=hashlib.sha256(b"reviewed author prompt").hexdigest(),
    )
    assert created
    proposal = steps.finish_model_step(
        owner,
        draft.id,
        step.id,
        proposed_value=_VALUE,
        provenance=f"model-step:{step.id}",
    )
    decided = drafts.decide_proposal(
        owner,
        draft.id,
        proposal.id,
        if_match=drafts.get_draft(owner, draft.id).etag,
        state=ProposalState.ACCEPTED,
        decided_value=None,
    )
    assert decided.decided_value == _VALUE
    objects = FilesystemObjectStore(tmp_path)
    publisher = SqlComposerRepository(engine, objects)
    current = publisher.get_draft(owner, draft.id)
    snapshot = RevisionSnapshot(
        draft.source,
        None,
        json.dumps({"content": _VALUE}, ensure_ascii=False),
        "{}",
        _MODEL,
        f"human:accepted:model-step:{step.id}",
        f"publish_proposal:{proposal.id}",
    )
    artifacts = (
        ArtifactContent("download", "text/markdown", _VALUE.encode()),
        ArtifactContent("preview", "text/markdown", _VALUE.encode()),
    )
    return (
        engine,
        objects,
        publisher,
        authors,
        owner,
        author_owner,
        shared,
        current,
        proposal.id,
        snapshot,
        artifacts,
    )


def _publish(  # noqa: PLR0913, PLR0917 - explicit publication fence inputs
    publisher: SqlComposerRepository,
    owner: UUID,
    current,
    proposal_id: UUID,
    snapshot: RevisionSnapshot,
    artifacts: tuple[ArtifactContent, ...],
):
    return publisher.publish_revision(
        owner,
        current.id,
        actor_id=owner,
        if_match=current.etag,
        idempotency_key="publish-with-author",
        snapshot=snapshot,
        artifacts=artifacts,
        approved_proposal_id=proposal_id,
    )


@pytest.mark.parametrize("stage", ["before-reserve", "during-artifact-write"])
@pytest.mark.parametrize("change", ["revoke", "version"])
def test_model_proposal_publication_fences_author_at_both_commits(
    tmp_path: Path, mocker: MockerFixture, stage: str, change: str
) -> None:
    (
        engine,
        objects,
        publisher,
        authors,
        owner,
        author_owner,
        shared,
        current,
        proposal_id,
        snapshot,
        artifacts,
    ) = _prepared(tmp_path)

    def change_author() -> None:
        if change == "revoke":
            authors.revoke(author_owner, shared.id, owner, if_match=shared.etag)
        else:
            authors.update(
                author_owner,
                shared.id,
                if_match=shared.etag,
                name="Ada",
                fields_json=json.dumps(
                    {"role": {"value": "Editor", "provenance": "human_edited"}}
                ),
            )

    try:
        if stage == "before-reserve":
            change_author()
        else:
            actual_put = objects.put
            changed = []

            def put_after_revocation(key: ObjectKey, content: bytes) -> None:
                actual_put(key, content)
                if not changed:
                    change_author()
                    changed.append(True)

            mocker.patch.object(objects, "put", side_effect=put_after_revocation)
        with pytest.raises(ComposerConflictError, match="Author access changed"):
            _publish(publisher, owner, current, proposal_id, snapshot, artifacts)
        latest = publisher.get_draft(owner, current.id)
        assert latest.current_revision_id == current.current_revision_id
        assert latest.content == current.content
        with Session(engine) as database:
            rows = database.scalars(
                select(ComposerRevisionRow).where(
                    ComposerRevisionRow.draft_id == str(current.id)
                )
            ).all()
            assert all(row.publication_state != "published" for row in rows)
            assert len(rows) == (0 if stage == "before-reserve" else 1)
    finally:
        engine.dispose()


def test_model_proposal_publication_with_current_author_grant(
    tmp_path: Path,
) -> None:
    (
        engine,
        _objects,
        publisher,
        _authors,
        owner,
        _author_owner,
        _shared,
        current,
        proposal_id,
        snapshot,
        artifacts,
    ) = _prepared(tmp_path)
    try:
        revision = _publish(publisher, owner, current, proposal_id, snapshot, artifacts)
        assert revision.snapshot.operation == f"publish_proposal:{proposal_id}"
        assert publisher.get_draft(owner, current.id).current_revision_id == revision.id
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("bad-origin", "origin changed"),
        ("missing-step", "origin changed"),
        ("invalid-refs", "Author access changed"),
        ("invalid-shape", "Author access changed"),
        ("duplicate-refs", "Author access changed"),
    ],
)
def test_model_proposal_rejects_invalid_origin_and_frozen_refs(
    tmp_path: Path, tamper: str, message: str
) -> None:
    (
        engine,
        _objects,
        publisher,
        _authors,
        owner,
        _author_owner,
        _shared,
        current,
        proposal_id,
        snapshot,
        artifacts,
    ) = _prepared(tmp_path)
    try:
        with Session(engine) as database, database.begin():
            proposal = database.get(ComposerProposalRow, str(proposal_id))
            assert proposal is not None
            step_id = UUID(proposal.provenance.removeprefix("model-step:"))
            step = database.get(ComposerModelStepRow, str(step_id))
            assert step is not None
            if tamper == "bad-origin":
                proposal.provenance = "model-step:not-a-uuid"
            elif tamper == "missing-step":
                proposal.provenance = f"model-step:{uuid4()}"
            elif tamper == "invalid-refs":
                step.author_refs = "{"
            elif tamper == "invalid-shape":
                step.author_refs = '{"id":"not-an-array"}'
            else:
                refs = json.loads(step.author_refs)
                step.author_refs = json.dumps([*refs, *refs])
        with pytest.raises(ComposerConflictError, match=message):
            _publish(publisher, owner, current, proposal_id, snapshot, artifacts)
        assert publisher.get_draft(owner, current.id).current_revision_id is None
        with Session(engine) as database:
            assert (
                database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.draft_id == str(current.id)
                    )
                )
                is None
            )
    finally:
        engine.dispose()
