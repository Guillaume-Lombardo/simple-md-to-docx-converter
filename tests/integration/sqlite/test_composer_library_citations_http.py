"""Cited library excerpts in model steps, with access fenced through publication.

Only the provider transport is replaced. The scenarios are shared with the
PostgreSQL/S3 profile tests.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from markweave.composer.library_prompt import LIBRARY_EXCERPTS_MARKER
from markweave.persistence.composer.library import LibraryDocument, LibrarySource
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore
from tests.integration.sqlite.test_composer_office_edit_steps_http import (
    _ENDPOINT,
    _MODEL,
    OfficeStepHarness,
    _headers,
    _settled,
    office_step_harness,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_OWN = "The northern turbine failed after the storm."
_SHARED = "Inspection confirmed corrosion in the gearbox."
_FOREIGN = "Private budget figures."


@pytest.fixture
def harness(tmp_path: Path, mocker: MockerFixture) -> Iterator[OfficeStepHarness]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    try:
        with office_step_harness(
            engine, FilesystemObjectStore(tmp_path), mocker, tmp_path
        ) as created:
            yield created
    finally:
        engine.dispose()


def _document(harness: OfficeStepHarness, owner: Any, text: str) -> LibraryDocument:
    return harness.library.create(
        owner.id,
        text.split()[1],
        LibrarySource(text.encode(), "text/markdown", "test:library", (text,)),
    )


def _draft(harness: OfficeStepHarness) -> tuple[str, str]:
    draft = harness.store.create_draft_with_source(
        harness.owner.id,
        b"# Report\n",
        "scanner-approved",
        title="Report",
        content="# Report\n",
        media_type="text/markdown",
    )
    return f"/api/v1/composer/drafts/{draft.id}", draft.etag


def _cite(*documents: LibraryDocument) -> list[dict[str, object]]:
    return [
        {"version_id": str(item.current_version_id), "chunk": 0} for item in documents
    ]


def _base(harness: OfficeStepHarness) -> dict[str, object]:
    return {
        "connection_id": str(harness.connection_id),
        "approved_endpoint": _ENDPOINT,
        "approved_model": _MODEL,
        "content": "Summarize the cited findings.",
        "max_output_tokens": 64,
    }


def _preview(
    harness: OfficeStepHarness, path: str, etag: str, refs: list[dict[str, object]]
) -> Any:
    return harness.client.post(
        f"{path}/model-steps/preview",
        json={**_base(harness), "author_ids": [], "library_refs": refs},
        headers={"X-CSRF-Token": "csrf", "If-Match": etag},
    )


def _start(  # noqa: PLR0913, PLR0917 - explicit reviewed request
    harness: OfficeStepHarness,
    path: str,
    etag: str,
    refs: list[dict[str, object]],
    digest: str,
    key: str,
) -> Any:
    return harness.client.post(
        f"{path}/model-steps",
        json={**_base(harness), "library_refs": refs, "author_preview_digest": digest},
        headers=_headers(etag, key),
    )


def exercise_cited_step_is_reviewed_and_published(harness: OfficeStepHarness) -> None:
    """Exactly the previewed excerpts are sent, recorded and fenced at publication."""

    own = _document(harness, harness.owner, _OWN)
    shared = _document(harness, harness.other, _SHARED)
    harness.library.grant(
        harness.other.id, shared.id, harness.owner.id, if_match=shared.etag
    )
    path, etag = _draft(harness)
    refs = _cite(own, shared)

    preview = _preview(harness, path, etag, refs)
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()
    assert reviewed["transmitted_content"].startswith("Summarize the cited findings.")
    assert LIBRARY_EXCERPTS_MARKER in reviewed["transmitted_content"]
    assert [item["text"] for item in reviewed["library_excerpts"]] == [_OWN, _SHARED]
    assert reviewed["author_refs"] == []

    harness.replies.append("# Report\n\nThe turbine failed [library:x#0].\n")
    started = _start(harness, path, etag, refs, reviewed["preview_digest"], "cited")
    assert started.status_code == 202, started.text
    assert started.json()["library_refs"] == refs
    step = _settled(harness, path, started.json()["id"])
    assert step["status"] == "completed", step
    assert step["library_refs"] == refs
    assert harness.prompts == [reviewed["transmitted_content"]]
    replayed = _start(harness, path, etag, refs, reviewed["preview_digest"], "cited")
    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == step
    reused = _start(harness, path, etag, refs[:1], reviewed["preview_digest"], "cited")
    assert reused.status_code == 412
    assert len(harness.prompts) == 1

    current = harness.client.get(path).json()
    proposal = step["proposal_id"]
    accepted = harness.client.post(
        f"{path}/proposals/{proposal}/decision",
        json={"state": "accepted"},
        headers={"X-CSRF-Token": "csrf", "If-Match": current["etag"]},
    )
    assert accepted.status_code == 200, accepted.text
    decided = harness.client.get(path).json()["etag"]
    current_shared = harness.library.get(harness.other.id, shared.id)
    harness.library.revoke(
        harness.other.id, shared.id, harness.owner.id, if_match=current_shared.etag
    )
    refused = harness.client.post(
        f"{path}/proposals/{proposal}/publish", headers=_headers(decided, "publish")
    )
    assert refused.status_code == 412
    revoked_retry = _start(
        harness, path, etag, refs, reviewed["preview_digest"], "cited"
    )
    assert revoked_retry.status_code == 412
    assert harness.client.get(path).json()["etag"] == decided

    restored = harness.library.get(harness.other.id, shared.id)
    harness.library.grant(
        harness.other.id, shared.id, harness.owner.id, if_match=restored.etag
    )
    published = harness.client.post(
        f"{path}/proposals/{proposal}/publish", headers=_headers(decided, "publish")
    )
    assert published.status_code == 201, published.text


def test_cited_step_is_reviewed_and_published(harness: OfficeStepHarness) -> None:
    exercise_cited_step_is_reviewed_and_published(harness)


def exercise_citation_access_is_rechecked(harness: OfficeStepHarness) -> None:
    """Foreign, revoked, changed or duplicated citations never reach the provider."""

    own = _document(harness, harness.owner, _OWN)
    foreign = _document(harness, harness.other, _FOREIGN)
    shared = _document(harness, harness.other, _SHARED)
    shared = harness.library.grant(
        harness.other.id, shared.id, harness.owner.id, if_match=shared.etag
    )
    path, etag = _draft(harness)

    assert _preview(harness, path, etag, _cite(foreign)).status_code == 412
    missing: list[dict[str, object]] = [
        {"version_id": str(own.current_version_id), "chunk": 7}
    ]
    assert _preview(harness, path, etag, missing).status_code == 412
    assert _preview(harness, path, etag, _cite(own, own)).status_code == 422
    assert _preview(harness, path, '"0"', _cite(own)).status_code == 412
    too_many: list[dict[str, object]] = [
        {"version_id": str(own.current_version_id), "chunk": chunk}
        for chunk in range(21)
    ]
    assert _preview(harness, path, etag, too_many).status_code == 422

    reviewed = _preview(harness, path, etag, _cite(own)).json()
    assert (
        _start(harness, path, etag, _cite(own), "0" * 64, "digest").status_code == 412
    )
    unreviewed = harness.client.post(
        f"{path}/model-steps",
        json={**_base(harness), "library_refs": _cite(own)},
        headers=_headers(etag, "unreviewed"),
    )
    assert unreviewed.status_code == 412
    harness.replies.append("# Report\n")
    accepted = _start(harness, path, etag, _cite(own), reviewed["preview_digest"], "ok")
    assert accepted.status_code == 202
    assert _settled(harness, path, accepted.json()["id"])["status"] == "completed"

    path, etag = _draft(harness)
    shared_preview = _preview(harness, path, etag, _cite(shared)).json()
    harness.library.revoke(
        harness.other.id, shared.id, harness.owner.id, if_match=shared.etag
    )
    revoked = _start(
        harness, path, etag, _cite(shared), shared_preview["preview_digest"], "revoked"
    )
    assert revoked.status_code == 412

    shared = harness.library.grant(
        harness.other.id,
        shared.id,
        harness.owner.id,
        if_match=harness.library.get(harness.other.id, shared.id).etag,
    )
    shared_preview = _preview(harness, path, etag, _cite(shared)).json()

    def revoke_during_call() -> None:
        harness.library.revoke(
            harness.other.id, shared.id, harness.owner.id, if_match=shared.etag
        )

    harness.before_chat.append(revoke_during_call)
    harness.replies.append("# Report\n")
    prompts = len(harness.prompts)
    started = _start(
        harness, path, etag, _cite(shared), shared_preview["preview_digest"], "late"
    )
    assert started.status_code == 202
    step = _settled(harness, path, started.json()["id"])
    assert step["status"] == "failed"
    assert step["proposal_id"] is None
    assert len(harness.prompts) == prompts


def test_citation_access_is_rechecked_before_egress(harness: OfficeStepHarness) -> None:
    exercise_citation_access_is_rechecked(harness)


def exercise_cited_question_resume(harness: OfficeStepHarness) -> None:
    """A cited question keeps its citations, and resuming must cite them again."""

    own = _document(harness, harness.owner, _OWN)
    path, etag = _draft(harness)
    refs = _cite(own)
    preview = _preview(harness, path, etag, refs).json()
    harness.replies.append("Which turbine should the summary cover?")
    started = harness.client.post(
        f"{path}/model-steps",
        json={
            **_base(harness),
            "intent": "question",
            "library_refs": refs,
            "author_preview_digest": preview["preview_digest"],
        },
        headers=_headers(etag, "question"),
    )
    assert started.status_code == 202, started.text
    step = _settled(harness, path, started.json()["id"])
    assert step["status"] == "completed", step
    question = harness.client.get(f"{path}/questions/{step['question_id']}").json()
    assert question["source_library_refs"] == refs
    listed = harness.client.get(f"{path}/questions").json()["questions"]
    assert listed[0]["source_library_refs"] == refs

    answered = harness.client.post(
        f"{path}/questions/{question['id']}/answer",
        json={"content": "The northern one."},
        headers=_headers(harness.client.get(path).json()["etag"], "answer"),
    )
    assert answered.status_code == 200, answered.text
    current = harness.client.get(path).json()["etag"]
    resume = {
        **_base(harness),
        "content": "Question: Which turbine?\nAnswer: The northern one.",
        "answered_question_id": question["id"],
    }
    uncited = harness.client.post(
        f"{path}/model-steps", json=resume, headers=_headers(current, "uncited")
    )
    assert uncited.status_code == 412
    cited_preview = harness.client.post(
        f"{path}/model-steps/preview",
        json={
            **_base(harness),
            "content": resume["content"],
            "author_ids": [],
            "library_refs": refs,
        },
        headers={"X-CSRF-Token": "csrf", "If-Match": current},
    )
    assert cited_preview.status_code == 200, cited_preview.text
    harness.replies.append("# Report\n")
    resumed = harness.client.post(
        f"{path}/model-steps",
        json={
            **resume,
            "library_refs": refs,
            "author_preview_digest": cited_preview.json()["preview_digest"],
        },
        headers=_headers(current, "resumed"),
    )
    assert resumed.status_code == 202, resumed.text
    assert _settled(harness, path, resumed.json()["id"])["status"] == "completed"


def test_cited_question_resume_requires_its_citations(
    harness: OfficeStepHarness,
) -> None:
    exercise_cited_question_resume(harness)
