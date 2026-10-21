"""Model-proposed Office edits over HTTP with real persistence, runner and executor.

Only the provider transport is replaced. The scenarios are shared with the
PostgreSQL/S3 profile tests.
"""

import json
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.connections import ConnectionService
from markweave.config import Settings
from markweave.http.composer_docx_edit_schemas import (
    ComposerOfficeEditStepCreateRequest,
)
from markweave.http.composer_step_runner import ComposerStepRunner
from markweave.http.routers.composer_model_steps import _payload_digest
from markweave.persistence.composer import (
    SqlComposerModelStepRepository,
    SqlComposerRepository,
    SqlConnectionRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerCredentialRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore, ObjectStore
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_ROOT = Path(__file__).resolve().parents[3]
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_ENDPOINT = "https://model.internal/v1"
_MODEL = "small"
_TITLE = "word/document.xml#t0"
_BOLD = "word/document.xml#t2"


@dataclass
class OfficeStepHarness:
    client: TestClient
    store: SqlComposerRepository
    authentication: Any
    owner: User
    other: User
    connection_id: UUID
    replies: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    steps: SqlComposerModelStepRepository | None = None


def _members(content: bytes) -> dict[str, bytes]:
    with ZipFile(BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _seed(engine: Engine, owner: User, other: User, connection_id: UUID) -> None:
    with Session(engine) as database, database.begin():
        for user in (owner, other):
            database.add(
                UserRow(
                    id=str(user.id),
                    username=user.username,
                    normalized_username=user.normalized_username,
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role=user.role.value,
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
        database.flush()
        database.add(
            ComposerConnectionRow(
                id=str(connection_id),
                scope="instance",
                owner_id=None,
                identity_mode="shared",
                name=f"Internal {connection_id}",
                endpoint=_ENDPOINT,
                selected_model=_MODEL,
                permitted_models=json.dumps([_MODEL]),
                enabled=True,
                version=1,
                generation=1,
                outage=False,
            )
        )
        database.flush()
        database.add(
            ComposerConnectionGrantRow(
                connection_id=str(connection_id), user_id=str(owner.id)
            )
        )
        database.add(
            ComposerCredentialRow(
                id=str(uuid4()),
                connection_id=str(connection_id),
                user_id=None,
                api_key=b"encrypted-key",
                client_certificate=None,
                client_private_key=None,
                ca_bundle=None,
                outage=False,
            )
        )


@contextmanager
def office_step_harness(
    engine: Engine, objects: ObjectStore, mocker: MockerFixture, tmp_path: Path
) -> Generator[OfficeStepHarness]:
    """Serve the real routes, runner and repositories with a scripted provider."""

    upgrade_database(engine)
    suffix = uuid4().hex[:12]
    owner = User(uuid4(), f"office-{suffix}", f"office-{suffix}", "hash", Role.USER)
    other = User(uuid4(), f"other-{suffix}", f"other-{suffix}", "hash", Role.USER)
    connection_id = uuid4()
    _seed(engine, owner, other, connection_id)
    store = SqlComposerRepository(engine, objects)
    steps = SqlComposerModelStepRepository(engine)
    harness_box: list[OfficeStepHarness] = []

    def chat(*_args: object, **kwargs: Any) -> dict[str, object]:
        kwargs["before_dispatch"]()
        harness = harness_box[0]
        harness.prompts.append(_args[2][0]["content"])  # ty: ignore[not-subscriptable]
        return {"choices": [{"message": {"content": harness.replies.pop(0)}}]}

    connections = mocker.Mock(spec=ConnectionService)
    connections.chat.side_effect = chat
    connections.get_visible.return_value = SimpleNamespace(
        enabled=True,
        endpoint=_ENDPOINT,
        selected_model=_MODEL,
        permitted_models=(_MODEL,),
    )
    permissions = mocker.Mock(spec=SqlConnectionRepository)
    permissions.can_manage_personal.return_value = False
    runner = ComposerStepRunner(
        steps, connections, maximum_active=4, lease=timedelta(seconds=30)
    )
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = owner
    authentication.authenticate.return_value = owner
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106 - isolated fixture
        storage_profile="standalone",
        standalone_data_directory=str(tmp_path),
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=4096,
        composer_http_request_max_bytes=16_384,
        composer_maximum_request_bytes=4096,
        composer_maximum_output_tokens=256,
    )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            composer_store=store,
            composer_model_step_repository=steps,
            composer_steps=runner,
            composer_connections=connections,
            composer_connection_repository=permissions,
        ),
    )
    try:
        with TestClient(app, base_url="https://testserver") as client:
            harness = OfficeStepHarness(
                client, store, authentication, owner, other, connection_id, steps=steps
            )
            harness_box.append(harness)
            yield harness
    finally:
        runner.close()


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


def _headers(etag: str, key: str) -> dict[str, str]:
    return {"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": key}


def _office_draft(
    harness: OfficeStepHarness, fixture: str = "docx/text.docx", media: str = _DOCX
) -> tuple[str, str, str, bytes]:
    source = (_ROOT / "spikes/anydoc/corpus" / fixture).read_bytes()
    draft = harness.store.create_draft_with_source(
        harness.owner.id,
        source,
        "scanner-approved",
        title="Office",
        content="",
        media_type=media,
    )
    path = f"/api/v1/composer/drafts/{draft.id}"
    captured = harness.client.post(
        f"{path}/revisions/from-source", headers=_headers(draft.etag, "source")
    )
    assert captured.status_code == 201
    return path, captured.json()["id"], captured.headers["ETag"], source


def _request(
    harness: OfficeStepHarness, targets: tuple[str, ...], **extra: object
) -> dict[str, object]:
    return {
        "connection_id": str(harness.connection_id),
        "approved_endpoint": _ENDPOINT,
        "approved_model": _MODEL,
        "instruction": "Make the selected text more formal.",
        "targets": list(targets),
        "max_output_tokens": 128,
        **extra,
    }


def _settled(harness: OfficeStepHarness, path: str, step_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        step = harness.client.get(f"{path}/model-steps/{step_id}").json()
        if step["status"] != "running":
            return step
        time.sleep(0.02)
    raise AssertionError("Model step did not settle")


def _run_step(  # noqa: PLR0913, PLR0917 - one explicit preview and start
    harness: OfficeStepHarness,
    path: str,
    revision_id: str,
    etag: str,
    targets: tuple[str, ...],
    reply: str,
    key: str = "step",
) -> dict[str, Any]:
    preview = harness.client.post(
        f"{path}/revisions/{revision_id}/office-edit-steps/preview",
        json=_request(harness, targets),
        headers={"If-Match": etag},
    )
    assert preview.status_code == 200, preview.text
    harness.replies.append(reply)
    started = harness.client.post(
        f"{path}/revisions/{revision_id}/office-edit-steps",
        json=_request(
            harness, targets, preview_digest=preview.json()["preview_digest"]
        ),
        headers=_headers(etag, key),
    )
    assert started.status_code == 202, started.text
    return _settled(harness, path, started.json()["id"])


def _reply(*operations: tuple[str, str]) -> str:
    return json.dumps(
        {
            "operations": [
                {"op": "replace_text", "target": target, "text": text}
                for target, text in operations
            ]
        }
    )


def _refuse_decisions(
    harness: OfficeStepHarness,
    path: str,
    proposal_id: str,
    proposed: dict[str, Any],
    etag: str,
) -> None:
    """Malformed or scope-changing human decisions change nothing."""

    client = harness.client
    for bad in (
        {**proposed, "allowed_targets": [_TITLE, _BOLD, "word/document.xml#t5"]},
        {
            **proposed,
            "operations": [
                {**proposed["operations"][0], "target": "word/document.xml#t5"}
            ],
        },
        {**proposed, "base_revision_id": str(uuid4())},
        {**proposed, "operations": [{**proposed["operations"][0], "op": "delete"}]},
        {**proposed, "format": []},
        {**proposed, "format": {}},
        {**proposed, "engine_version": True},
        {**proposed, "base_revision_id": {}},
        {**proposed, "operations": [{**proposed["operations"][0], "target": []}]},
        [],
        None,
    ):
        refused = client.post(
            f"{path}/proposals/{proposal_id}/decision",
            json={"state": "edited", "decided_value": json.dumps(bad)},
            headers={"X-CSRF-Token": "csrf", "If-Match": etag},
        )
        assert refused.status_code == 422, bad
        assert refused.json()["error"]["code"] == "COMPOSER_REQUEST_INVALID"
    assert client.get(path).json()["etag"] == etag
    unchanged = client.get(f"{path}/proposals/{proposal_id}").json()
    assert (unchanged["state"], unchanged["decided_value"]) == ("pending", None)


def exercise_model_proposed_edit(harness: OfficeStepHarness) -> None:
    """Preview, transmit, review and publish one exact model-proposed edit."""

    client = harness.client
    path, revision_id, etag, source = _office_draft(harness)

    preview = client.post(
        f"{path}/revisions/{revision_id}/office-edit-steps/preview",
        json=_request(harness, (_TITLE, _BOLD)),
        headers={"If-Match": etag},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["format"] == "docx"
    assert body["base_revision_id"] == revision_id
    assert body["targets"] == [
        {"target": _TITLE, "text": "Fixture Document"},
        {"target": _BOLD, "text": "bold"},
    ]
    assert "Fixture Document" in body["transmitted_content"]
    assert "Plain paragraph with" not in body["transmitted_content"]

    step = _run_step(
        harness,
        path,
        revision_id,
        etag,
        (_TITLE, _BOLD),
        "```json\n" + _reply((_TITLE, "Formal Fixture Document")) + "\n```",
    )
    assert harness.prompts == [body["transmitted_content"]]
    assert step["status"] == "completed"
    assert step["intent"] == "proposal"
    proposal = client.get(f"{path}/proposals/{step['proposal_id']}").json()
    assert proposal["state"] == "pending"
    proposed = json.loads(proposal["proposed_value"])
    assert proposed["kind"] == "office_edit"
    assert proposed["allowed_targets"] == [_TITLE, _BOLD]
    assert proposed["operations"] == [
        {
            "op": "replace_text",
            "target": _TITLE,
            "expected_text": "Fixture Document",
            "text": "Formal Fixture Document",
        }
    ]
    publish_path = f"{path}/proposals/{proposal['id']}/office-edits"
    current = client.get(path).json()
    assert (
        client.post(
            publish_path, headers=_headers(current["etag"], "early")
        ).status_code
        == 422
    )

    _refuse_decisions(harness, path, proposal["id"], proposed, current["etag"])
    edited_value = {
        **proposed,
        "operations": [{**proposed["operations"][0], "text": "Human Title"}],
    }
    decided = client.post(
        f"{path}/proposals/{proposal['id']}/decision",
        json={"state": "edited", "decided_value": json.dumps(edited_value, indent=2)},
        headers={"X-CSRF-Token": "csrf", "If-Match": current["etag"]},
    )
    assert decided.status_code == 200
    assert json.loads(decided.json()["decided_value"]) == edited_value
    assert "\n" not in decided.json()["decided_value"]
    after_decision = client.get(path).json()
    markdown = client.post(
        f"{path}/proposals/{proposal['id']}/publish",
        headers=_headers(after_decision["etag"], "markdown"),
    )
    assert markdown.status_code == 422

    published = client.post(
        publish_path, headers=_headers(after_decision["etag"], "publish")
    )
    assert published.status_code == 201, published.text
    revision = published.json()
    assert revision["operation"] == f"office_edit_proposal:{proposal['id']}"
    assert revision["model_identity"] == _MODEL
    assert revision["provenance"] == f"human:edited:model-step:{step['id']}"
    assert revision["approved_values"] == decided.json()["decided_value"]
    download = client.get(
        f"{path}/revisions/{revision['id']}/artifacts/download"
    ).content
    before, after = _members(source), _members(download)
    assert {name for name in before if before[name] != after[name]} == {
        "word/document.xml"
    }
    assert b"<w:t>Human Title</w:t>" in after["word/document.xml"]
    assert (
        client.post(
            publish_path, headers=_headers(after_decision["etag"], "publish")
        ).json()
        == revision
    )
    assert (
        client.post(
            publish_path, headers=_headers(published.headers["ETag"], "again")
        ).status_code
        == 412
    )


def test_model_proposed_edit_is_reviewed_and_published_exactly(
    harness: OfficeStepHarness,
) -> None:
    exercise_model_proposed_edit(harness)


TOOL_MISUSE = [
    "Sure! I changed the title.",
    _reply(("word/document.xml#t5", "Injected")),
    json.dumps({"operations": [{"op": "delete", "target": _TITLE, "text": ""}]}),
    json.dumps(
        {
            "operations": [
                {"op": "replace_text", "target": _TITLE, "text": "x", "path": "/etc"}
            ]
        }
    ),
    json.dumps({"operations": [], "shell": "rm -rf /"}),
    _reply((_TITLE, "a"), (_TITLE, "b")),
    _reply((_TITLE, "line\nbreak")),
    json.dumps([{"op": "replace_text", "target": _TITLE, "text": "x"}]),
]


def exercise_tool_misuse(harness: OfficeStepHarness, reply: str) -> None:
    """Any reply outside the allowlisted tool contract fails without a proposal."""

    path, revision_id, etag, _source = _office_draft(harness)

    step = _run_step(harness, path, revision_id, etag, (_TITLE,), reply)

    assert step["status"] == "failed"
    assert step["error_code"] == "provider_invalid"
    assert step["proposal_id"] is None
    assert harness.client.get(f"{path}/proposals").json()["proposals"] == []


@pytest.mark.parametrize("reply", TOOL_MISUSE)
def test_tool_misuse_and_prompt_injection_fail_closed(
    harness: OfficeStepHarness, reply: str
) -> None:
    exercise_tool_misuse(harness, reply)


def exercise_stale_and_invalid_requests(harness: OfficeStepHarness) -> None:
    """Preview and start refuse stale, foreign, ineligible and mismatched input."""

    client = harness.client
    path, revision_id, etag, _source = _office_draft(harness)
    preview_path = f"{path}/revisions/{revision_id}/office-edit-steps/preview"
    start_path = f"{path}/revisions/{revision_id}/office-edit-steps"

    assert (
        client.post(preview_path, json=_request(harness, (_TITLE,))).status_code == 428
    )
    assert (
        client.post(
            preview_path, json=_request(harness, (_TITLE,)), headers={"If-Match": '"9"'}
        ).status_code
        == 412
    )
    for targets, status in (
        (("word/document.xml#t99999",), 422),
        ((_TITLE, _TITLE), 422),
        (("word/styles.xml#t0",), 422),
    ):
        assert (
            client.post(
                preview_path,
                json=_request(harness, targets),
                headers={"If-Match": etag},
            ).status_code
            == status
        )
    assert (
        client.post(
            start_path,
            json=_request(harness, (_TITLE,), preview_digest="0" * 64),
            headers=_headers(etag, "wrong-digest"),
        ).status_code
        == 412
    )
    assert harness.prompts == []

    edited = client.post(
        f"{path}/revisions/{revision_id}/docx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": _BOLD,
                    "expected_text": "bold",
                    "text": "strong",
                }
            ]
        },
        headers=_headers(etag, "direct"),
    )
    assert edited.status_code == 201
    assert (
        client.post(
            preview_path,
            json=_request(harness, (_TITLE,)),
            headers={"If-Match": edited.headers["ETag"]},
        ).status_code
        == 412
    )

    markdown = harness.store.create_draft_with_source(
        harness.owner.id,
        b"# Source\n",
        "scanner-approved",
        title="Markdown",
        content="# Source\n",
        media_type="text/markdown",
    )
    markdown_path = f"/api/v1/composer/drafts/{markdown.id}"
    captured = client.post(
        f"{markdown_path}/revisions/from-source",
        headers=_headers(markdown.etag, "md"),
    )
    assert (
        client.post(
            f"{markdown_path}/revisions/{captured.json()['id']}"
            "/office-edit-steps/preview",
            json=_request(harness, (_TITLE,)),
            headers={"If-Match": captured.headers["ETag"]},
        ).status_code
        == 422
    )

    harness.authentication.authenticate.return_value = harness.other
    assert (
        client.post(
            preview_path,
            json=_request(harness, (_TITLE,)),
            headers={"If-Match": edited.headers["ETag"]},
        ).status_code
        == 404
    )
    harness.authentication.authenticate.return_value = harness.owner


def test_stale_foreign_and_ineligible_requests_never_transmit(
    harness: OfficeStepHarness,
) -> None:
    exercise_stale_and_invalid_requests(harness)


def exercise_superseded_proposal(harness: OfficeStepHarness) -> None:
    """A revision published after review makes the proposal unpublishable."""

    client = harness.client
    path, revision_id, etag, _source = _office_draft(harness, "pptx/pres.pptx", _PPTX)
    title = "ppt/slides/slide1.xml#t0"
    step = _run_step(
        harness, path, revision_id, etag, (title,), _reply((title, "Model deck"))
    )
    proposal_id = step["proposal_id"]
    current = client.get(path).json()
    accepted = client.post(
        f"{path}/proposals/{proposal_id}/decision",
        json={"state": "accepted"},
        headers={"X-CSRF-Token": "csrf", "If-Match": current["etag"]},
    )
    assert accepted.status_code == 200
    reviewed = client.get(path).json()
    direct = client.post(
        f"{path}/revisions/{revision_id}/pptx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": "ppt/slides/slide2.xml#t0",
                    "expected_text": "Numbers Slide",
                    "text": "Numbers",
                }
            ]
        },
        headers=_headers(reviewed["etag"], "direct"),
    )
    assert direct.status_code == 201
    stale = client.post(
        f"{path}/proposals/{proposal_id}/office-edits",
        headers=_headers(direct.headers["ETag"], "stale"),
    )
    assert stale.status_code == 412
    assert len(client.get(f"{path}/revisions").json()["revisions"]) == 2


def test_superseded_proposals_cannot_be_published(
    harness: OfficeStepHarness,
) -> None:
    exercise_superseded_proposal(harness)


def exercise_pptx_proposal(harness: OfficeStepHarness) -> None:
    """Accepted PPTX proposals publish only the reviewed notes text."""

    client = harness.client
    path, revision_id, etag, source = _office_draft(harness, "pptx/pres.pptx", _PPTX)
    notes = "ppt/notesSlides/notesSlide1.xml#t0"
    step = _run_step(
        harness, path, revision_id, etag, (notes,), _reply((notes, "Model note"))
    )
    current = client.get(path).json()
    client.post(
        f"{path}/proposals/{step['proposal_id']}/decision",
        json={"state": "accepted"},
        headers={"X-CSRF-Token": "csrf", "If-Match": current["etag"]},
    )
    reviewed = client.get(path).json()
    published = client.post(
        f"{path}/proposals/{step['proposal_id']}/office-edits",
        headers=_headers(reviewed["etag"], "publish"),
    )
    assert published.status_code == 201
    revision = published.json()
    assert revision["provenance"] == f"human:accepted:model-step:{step['id']}"
    download = client.get(
        f"{path}/revisions/{revision['id']}/artifacts/download"
    ).content
    before, after = _members(source), _members(download)
    assert {name for name in before if before[name] != after[name]} == {
        "ppt/notesSlides/notesSlide1.xml"
    }


def test_pptx_notes_proposals_publish_exact_bytes(
    harness: OfficeStepHarness,
) -> None:
    exercise_pptx_proposal(harness)


def exercise_markdown_step_is_not_an_office_edit(harness: OfficeStepHarness) -> None:
    """A plain proposal step whose text looks like a tool reply stays Markdown."""

    client = harness.client
    path, _revision_id, etag, _source = _office_draft(harness)
    harness.replies.append(_reply((_TITLE, "Spoofed")))
    started = client.post(
        f"{path}/model-steps",
        json={
            "connection_id": str(harness.connection_id),
            "approved_endpoint": _ENDPOINT,
            "approved_model": _MODEL,
            "content": "Suggest Markdown.",
            "max_output_tokens": 64,
        },
        headers=_headers(etag, "markdown-step"),
    )
    assert started.status_code == 202
    step = _settled(harness, path, started.json()["id"])
    current = client.get(path).json()
    client.post(
        f"{path}/proposals/{step['proposal_id']}/decision",
        json={"state": "accepted"},
        headers={"X-CSRF-Token": "csrf", "If-Match": current["etag"]},
    )
    reviewed = client.get(path).json()
    refused = client.post(
        f"{path}/proposals/{step['proposal_id']}/office-edits",
        headers=_headers(reviewed["etag"], "spoof"),
    )
    assert refused.status_code == 422


def test_markdown_steps_cannot_masquerade_as_office_edits(
    harness: OfficeStepHarness,
) -> None:
    exercise_markdown_step_is_not_an_office_edit(harness)


def exercise_request_edges(harness: OfficeStepHarness) -> None:
    """Limits, blank instructions, disabled connections and reused keys fail closed."""

    client = harness.client
    path, revision_id, etag, _source = _office_draft(harness)
    preview_path = f"{path}/revisions/{revision_id}/office-edit-steps/preview"
    start_path = f"{path}/revisions/{revision_id}/office-edit-steps"

    assert (
        client.post(
            preview_path,
            json={**_request(harness, (_TITLE,)), "instruction": "   "},
            headers={"If-Match": etag},
        ).status_code
        == 422
    )
    assert (
        client.post(
            start_path,
            json=_request(
                harness, (_TITLE,), preview_digest="0" * 64, max_output_tokens=257
            ),
            headers=_headers(etag, "too-many-tokens"),
        ).status_code
        == 422
    )
    visible = harness.client.app.state.components.composer_connections
    enabled = visible.get_visible.return_value
    visible.get_visible.return_value = SimpleNamespace(
        **{**vars(enabled), "enabled": False}
    )
    assert (
        client.post(
            preview_path, json=_request(harness, (_TITLE,)), headers={"If-Match": etag}
        ).status_code
        == 412
    )
    visible.get_visible.return_value = enabled

    direct = client.post(
        f"{path}/revisions/{revision_id}/docx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": _BOLD,
                    "expected_text": "bold",
                    "text": "strong",
                }
            ]
        },
        headers=_headers(etag, "shared-key"),
    )
    assert direct.status_code == 201
    assert (
        client.post(
            f"{path}/proposals/{uuid4()}/office-edits",
            headers=_headers(direct.headers["ETag"], "shared-key"),
        ).status_code
        == 412
    )

    harness.replies.append("A plain Markdown suggestion.")
    started = client.post(
        f"{path}/model-steps",
        json={
            "connection_id": str(harness.connection_id),
            "approved_endpoint": _ENDPOINT,
            "approved_model": _MODEL,
            "content": "Suggest Markdown.",
            "max_output_tokens": 64,
        },
        headers=_headers(direct.headers["ETag"], "plain-step"),
    )
    step = _settled(harness, path, started.json()["id"])
    current = client.get(path).json()
    edited = client.post(
        f"{path}/proposals/{step['proposal_id']}/decision",
        json={"state": "edited", "decided_value": "Human Markdown"},
        headers={"X-CSRF-Token": "csrf", "If-Match": current["etag"]},
    )
    assert edited.status_code == 200
    assert edited.json()["decided_value"] == "Human Markdown"


def test_request_edges_fail_closed(harness: OfficeStepHarness) -> None:
    exercise_request_edges(harness)


def exercise_retried_steps(harness: OfficeStepHarness) -> None:
    """Retries return the admitted step without another call; key reuse fails."""

    client = harness.client
    path, revision_id, etag, _source = _office_draft(harness)
    step_path = f"{path}/revisions/{revision_id}/office-edit-steps"
    preview = client.post(
        f"{step_path}/preview",
        json=_request(harness, (_TITLE,)),
        headers={"If-Match": etag},
    ).json()
    body = _request(harness, (_TITLE,), preview_digest=preview["preview_digest"])
    harness.replies.append(_reply((_TITLE, "Formal Title")))
    started = client.post(step_path, json=body, headers=_headers(etag, "retry"))
    assert started.status_code == 202
    step = _settled(harness, path, started.json()["id"])
    assert step["status"] == "completed"
    prompts = len(harness.prompts)

    replayed = client.post(step_path, json=body, headers=_headers(etag, "retry"))
    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == step
    decided = client.post(
        f"{path}/proposals/{step['proposal_id']}/decision",
        json={"state": "rejected"},
        headers={"X-CSRF-Token": "csrf", "If-Match": client.get(path).json()["etag"]},
    )
    assert decided.status_code == 200
    later = client.post(step_path, json=body, headers=_headers(etag, "retry"))
    assert later.status_code == 202
    assert later.json()["id"] == step["id"]
    for reused, headers in (
        ({**body, "instruction": "Another instruction."}, _headers(etag, "retry")),
        (body, _headers('"999"', "retry")),
    ):
        refused = client.post(step_path, json=reused, headers=headers)
        assert refused.status_code == 412, refused.text
    harness.authentication.authenticate.return_value = harness.other
    try:
        foreign = client.post(step_path, json=body, headers=_headers(etag, "retry"))
        assert foreign.status_code == 404
    finally:
        harness.authentication.authenticate.return_value = harness.owner

    _exercise_admission_race(harness, step_path, body)
    prompts = len(harness.prompts)

    current = client.get(path).json()
    assert harness.steps is not None
    running_body = ComposerOfficeEditStepCreateRequest.model_validate(
        {**body, "instruction": "Still running."}
    )
    running, created = harness.steps.start_model_step(
        harness.owner.id,
        UUID(current["id"]),
        connection_id=harness.connection_id,
        approved_endpoint=_ENDPOINT,
        approved_model=_MODEL,
        if_match=current["etag"],
        idempotency_key="running",
        payload_digest=_payload_digest(running_body, 4096),
        max_active=4,
        lease=timedelta(minutes=5),
        intent="office_edit",
    )
    assert created
    in_flight = client.post(
        step_path,
        json=running_body.model_dump(mode="json"),
        headers=_headers(current["etag"], "running"),
    )
    assert in_flight.status_code == 202, in_flight.text
    assert (in_flight.json()["id"], in_flight.json()["status"]) == (
        str(running.id),
        "running",
    )
    assert len(harness.prompts) == prompts


def _exercise_admission_race(
    harness: OfficeStepHarness, step_path: str, body: dict[str, object]
) -> None:
    """An identical request that completes after the replay lookup is replayed."""

    client = harness.client
    path = step_path.split("/revisions/", 1)[0]
    etag = client.get(path).json()["etag"]
    assert harness.steps is not None
    lookup = harness.steps.replay_model_step
    paused = [True]
    winners: list[dict[str, object]] = []

    def lookup_then_lose_the_race(*args: Any, **kwargs: Any) -> object:
        found = lookup(*args, **kwargs)
        if paused:
            # Pause the first request right after its lookup found nothing and
            # let an identical request be admitted and complete meanwhile.
            paused.clear()
            harness.replies.append(_reply((_TITLE, "Raced Title")))
            winner = client.post(step_path, json=body, headers=_headers(etag, "raced"))
            assert winner.status_code == 202, winner.text
            winners.append(_settled(harness, path, winner.json()["id"]))
            assert client.get(path).json()["etag"] != etag
        return found

    prompts = len(harness.prompts)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(harness.steps, "replay_model_step", lookup_then_lose_the_race)
        loser = client.post(step_path, json=body, headers=_headers(etag, "raced"))
    assert loser.status_code == 202, loser.text
    assert winners
    assert winners[0]["status"] == "completed"
    assert loser.json() == winners[0]
    assert len(harness.prompts) == prompts + 1

    reused = client.post(
        step_path,
        json={**body, "instruction": "A different instruction."},
        headers=_headers(etag, "raced"),
    )
    assert reused.status_code == 412, reused.text


def test_retried_steps_replay_without_another_model_call(
    harness: OfficeStepHarness,
) -> None:
    exercise_retried_steps(harness)
