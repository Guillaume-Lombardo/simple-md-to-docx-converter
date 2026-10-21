"""Exercise bounded DOCX text edits over HTTP against SQLite and filesystem storage.

The scenario functions are shared with the PostgreSQL/S3 profile tests.
"""

from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4
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
from markweave.composer.drafts import ComposerDraft
from markweave.config import Settings
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore, ObjectStore
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SOURCE = Path(__file__).resolve().parents[3] / "spikes/anydoc/corpus/docx/text.docx"
_MAIN = "word/document.xml"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DECK = Path(__file__).resolve().parents[3] / "spikes/anydoc/corpus/pptx/pres.pptx"


@dataclass
class DocxEditHarness:
    client: TestClient
    store: SqlComposerRepository
    authentication: object
    owner: User
    other: User


def _members(content: bytes) -> dict[str, bytes]:
    with ZipFile(BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _repack(source: bytes, replacements: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(output, "w") as target:
        for info in original.infolist():
            target.writestr(info, replacements.get(info.filename, original.read(info)))
    return output.getvalue()


@contextmanager
def docx_edit_harness(
    engine: Engine, objects: ObjectStore, mocker: MockerFixture, tmp_path: Path
) -> Generator[DocxEditHarness]:
    """Serve the real routes over one metadata engine and object store."""

    upgrade_database(engine)
    store = SqlComposerRepository(engine, objects)
    suffix = uuid4().hex[:12]
    owner = User(
        uuid4(), f"docx-owner-{suffix}", f"docx-owner-{suffix}", "hash", Role.USER
    )
    other = User(
        uuid4(), f"docx-other-{suffix}", f"docx-other-{suffix}", "hash", Role.USER
    )
    with Session(engine) as database, database.begin():
        for candidate in (owner, other):
            database.add(
                UserRow(
                    id=str(candidate.id),
                    username=candidate.username,
                    normalized_username=candidate.normalized_username,
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role=candidate.role.value,
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
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
        composer_upload_max_bytes=2048,
        composer_http_request_max_bytes=4096,
        composer_maximum_request_bytes=512,
    )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            composer_store=store,
        ),
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield DocxEditHarness(client, store, authentication, owner, other)


@pytest.fixture
def harness(tmp_path: Path, mocker: MockerFixture) -> Iterator[DocxEditHarness]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    try:
        with docx_edit_harness(
            engine, FilesystemObjectStore(tmp_path), mocker, tmp_path
        ) as created:
            yield created
    finally:
        engine.dispose()


def _docx_draft(
    harness: DocxEditHarness,
    source: bytes | None = None,
    media_type: str = _DOCX,
) -> tuple[ComposerDraft, dict[str, object], str]:
    draft = harness.store.create_draft_with_source(
        harness.owner.id,
        _SOURCE.read_bytes() if source is None else source,
        "scanner-approved",
        title="Report",
        content="",
        media_type=media_type,
    )
    captured = harness.client.post(
        f"/api/v1/composer/drafts/{draft.id}/revisions/from-source",
        headers={
            "X-CSRF-Token": "csrf",
            "If-Match": draft.etag,
            "Idempotency-Key": "source-1",
        },
    )
    assert captured.status_code == 201
    return draft, captured.json(), captured.headers["ETag"]


def exercise_edit_workflow(  # noqa: PLR0915 - one complete workflow
    harness: DocxEditHarness,
) -> None:
    client = harness.client
    draft, base, etag = _docx_draft(harness)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"

    listed = client.get(f"{path}/{base['id']}/docx-text-targets?limit=2&offset=1")
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "private, no-store"
    page = listed.json()
    assert page["revision_id"] == base["id"]
    assert page["download_sha256"] == draft.source.sha256
    assert page["engine_version"] == 1
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert page["total"] > 10
    assert page["targets"] == [
        {
            "target": "word/document.xml#t1",
            "paragraph": 1,
            "in_table": False,
            "text": "Plain paragraph with ",
        },
        {
            "target": "word/document.xml#t2",
            "paragraph": 1,
            "in_table": False,
            "text": "bold",
        },
    ]
    every = client.get(f"{path}/{base['id']}/docx-text-targets?limit=500").json()
    cell = next(item for item in every["targets"] if item["in_table"])

    request = {
        "operations": [
            {
                "op": "replace_text",
                "target": "word/document.xml#t2",
                "expected_text": "bold",
                "text": "strong & clear",
            },
            {
                "op": "replace_text",
                "target": cell["target"],
                "expected_text": cell["text"],
                "text": "Edited cell",
            },
        ]
    }
    headers = {"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": "edit-1"}
    edited = client.post(
        f"{path}/{base['id']}/docx-edits", json=request, headers=headers
    )
    assert edited.status_code == 201
    revision = edited.json()
    assert revision["number"] == 2
    assert revision["operation"] == f"docx_edit:{base['id']}"
    assert revision["provenance"] == "human:docx_edit"
    assert revision["model_identity"] is None
    assert revision["source_sha256"] == draft.source.sha256
    assert edited.headers["Location"] == f"{path}/{revision['id']}"
    assert edited.headers["ETag"] != etag
    assert '"text":"strong & clear"' in revision["approved_values"]

    download = client.get(f"{path}/{revision['id']}/artifacts/download")
    preview = client.get(f"{path}/{revision['id']}/artifacts/preview")
    assert download.status_code == 200
    assert download.headers["Content-Type"] == _DOCX
    assert download.content == preview.content
    before, after = _members(_SOURCE.read_bytes()), _members(download.content)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == {_MAIN}
    assert b"<w:t>strong &amp; clear</w:t>" in after[_MAIN]
    reread = client.get(f"{path}/{revision['id']}/docx-text-targets?limit=500").json()
    texts = {item["target"]: item["text"] for item in reread["targets"]}
    assert texts["word/document.xml#t2"] == "strong & clear"
    assert texts[cell["target"]] == "Edited cell"
    assert reread["total"] == every["total"]

    replay = client.post(
        f"{path}/{base['id']}/docx-edits", json=request, headers=headers
    )
    assert replay.status_code == 201
    assert replay.json() == revision
    reused = client.post(
        f"{path}/{base['id']}/docx-edits",
        json={"operations": request["operations"][:1]},
        headers=headers,
    )
    assert reused.status_code == 412

    stale_base = client.post(
        f"{path}/{base['id']}/docx-edits",
        json=request,
        headers={
            **headers,
            "If-Match": edited.headers["ETag"],
            "Idempotency-Key": "edit-2",
        },
    )
    assert stale_base.status_code == 412
    stale_etag = client.post(
        f"{path}/{revision['id']}/docx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": "word/document.xml#t0",
                    "expected_text": "Fixture Document",
                    "text": "Title",
                }
            ]
        },
        headers={**headers, "Idempotency-Key": "edit-3"},
    )
    assert stale_etag.status_code == 412

    diff = client.get(
        f"{path}/{revision['id']}/diff?from_revision_id={base['id']}"
    ).json()
    assert diff["status"] == "available"
    assert diff["scope"] == "artifact"
    assert [(item["before_text"], item["after_text"]) for item in diff["changes"]] == [
        (
            "Plain paragraph with bold, italic, and struck runs.\n",
            "Plain paragraph with strong & clear, italic, and struck runs.\n",
        ),
        (f"{cell['text']}\n", "Edited cell\n"),
    ]

    restored = client.post(
        f"{path}/{base['id']}/restore",
        headers={
            **headers,
            "If-Match": edited.headers["ETag"],
            "Idempotency-Key": "r-1",
        },
    )
    assert restored.status_code == 201
    assert (
        client.get(f"{path}/{restored.json()['id']}/artifacts/download").content
        == _SOURCE.read_bytes()
    )
    history = client.get(path).json()["revisions"]
    assert [item["operation"] for item in history] == [
        "capture_source",
        f"docx_edit:{base['id']}",
        "restore",
    ]


REJECTIONS = [
    (
        {"target": "word/document.xml#t0", "expected_text": "Stale", "text": "x"},
        422,
        "COMPOSER_DOCX_EDIT_TARGET_CHANGED",
        ["word/document.xml#t0"],
    ),
    (
        {"target": "word/document.xml#t99999", "expected_text": "", "text": "x"},
        422,
        "COMPOSER_DOCX_EDIT_UNSUPPORTED_TARGET",
        ["word/document.xml#t99999"],
    ),
    (
        {
            "target": "word/document.xml#t0",
            "expected_text": "Fixture Document",
            "text": "line\nbreak",
        },
        422,
        "COMPOSER_DOCX_EDIT_INVALID_TEXT",
        ["word/document.xml#t0"],
    ),
    (
        {
            "target": "word/document.xml#t0",
            "expected_text": "Fixture Document",
            "text": "x" * 513,
        },
        422,
        "COMPOSER_DOCX_EDIT_INVALID_TEXT",
        ["word/document.xml#t0"],
    ),
]


def exercise_rejection(
    harness: DocxEditHarness,
    operation: dict[str, str],
    status: int,
    code: str,
    paths: list[str],
) -> None:
    draft, base, etag = _docx_draft(harness)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"

    rejected = harness.client.post(
        f"{path}/{base['id']}/docx-edits",
        json={"operations": [{"op": "replace_text", **operation}]},
        headers={"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": "e"},
    )

    assert rejected.status_code == status
    assert rejected.json()["error"] == {
        "code": code,
        "message": "The DOCX edit cannot be applied.",
        "paths": paths,
    }
    assert rejected.headers["Cache-Control"] == "private, no-store"
    assert harness.store.get_draft(harness.owner.id, draft.id).etag == etag
    assert len(harness.client.get(path).json()["revisions"]) == 1


def exercise_request_validation(
    harness: DocxEditHarness,
) -> None:
    client = harness.client
    draft, base, etag = _docx_draft(harness)
    url = f"/api/v1/composer/drafts/{draft.id}/revisions/{base['id']}/docx-edits"
    valid = {
        "operations": [
            {
                "op": "replace_text",
                "target": "word/document.xml#t0",
                "expected_text": "Fixture Document",
                "text": "x",
            }
        ]
    }
    headers = {"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": "e"}

    assert (
        client.post(url, json=valid, headers={"X-CSRF-Token": "csrf"}).status_code
        == 428
    )
    assert (
        client.post(
            url, json=valid, headers={**headers, "Idempotency-Key": "two words"}
        ).status_code
        == 422
    )
    for body in (
        {"operations": []},
        {"operations": [{**valid["operations"][0], "op": "delete_text"}]},
        {"operations": [{**valid["operations"][0], "command": "rm -rf /"}]},
        {"operations": [{**valid["operations"][0], "target": "x" * 65}]},
    ):
        assert client.post(url, json=body, headers=headers).status_code == 422
    assert (
        len(
            client.get(f"/api/v1/composer/drafts/{draft.id}/revisions").json()[
                "revisions"
            ]
        )
        == 1
    )


def exercise_markdown_refusal(harness: DocxEditHarness) -> None:
    client = harness.client
    draft = harness.store.create_draft_with_source(
        harness.owner.id,
        b"# Source\n",
        "scanner-approved",
        title="Markdown",
        content="# Source\n",
        media_type="text/markdown",
    )
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"
    captured = client.post(
        f"{path}/from-source",
        headers={
            "X-CSRF-Token": "csrf",
            "If-Match": draft.etag,
            "Idempotency-Key": "s",
        },
    )
    revision_id = captured.json()["id"]

    assert client.get(f"{path}/{revision_id}/docx-text-targets").status_code == 422
    edited = client.post(
        f"{path}/{revision_id}/docx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": "word/document.xml#t0",
                    "expected_text": "",
                    "text": "x",
                }
            ]
        },
        headers={
            "X-CSRF-Token": "csrf",
            "If-Match": captured.headers["ETag"],
            "Idempotency-Key": "e",
        },
    )
    assert edited.status_code == 422


def exercise_owner_fence(harness: DocxEditHarness) -> None:
    draft, base, etag = _docx_draft(harness)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions/{base['id']}"
    harness.authentication.authenticate.return_value = harness.other  # ty: ignore[unresolved-attribute]

    assert harness.client.get(f"{path}/docx-text-targets").status_code == 404
    edited = harness.client.post(
        f"{path}/docx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": "word/document.xml#t0",
                    "expected_text": "Fixture Document",
                    "text": "Taken",
                }
            ]
        },
        headers={"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": "e"},
    )
    assert edited.status_code == 404
    assert harness.store.get_draft(harness.owner.id, draft.id).etag == etag


def test_text_edit_publishes_an_exact_new_revision_and_preserves_the_package(
    harness: DocxEditHarness,
) -> None:
    exercise_edit_workflow(harness)


def test_requests_are_validated_before_any_document_work(
    harness: DocxEditHarness,
) -> None:
    exercise_request_validation(harness)


def test_markdown_revisions_are_not_docx_edit_targets(harness: DocxEditHarness) -> None:
    exercise_markdown_refusal(harness)


def test_other_users_cannot_read_or_edit_a_revision(harness: DocxEditHarness) -> None:
    exercise_owner_fence(harness)


@pytest.mark.parametrize(("operation", "status", "code", "paths"), REJECTIONS)
def test_rejected_edits_leave_the_current_revision_intact(
    harness: DocxEditHarness,
    operation: dict[str, str],
    status: int,
    code: str,
    paths: list[str],
) -> None:
    exercise_rejection(harness, operation, status, code, paths)


def exercise_concurrent_edits(harness: DocxEditHarness) -> None:
    """Two edits racing on one revision publish exactly one new revision."""

    draft, base, etag = _docx_draft(harness)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"

    def edit(index: int) -> int:
        return harness.client.post(
            f"{path}/{base['id']}/docx-edits",
            json={
                "operations": [
                    {
                        "op": "replace_text",
                        "target": "word/document.xml#t0",
                        "expected_text": "Fixture Document",
                        "text": f"Concurrent title {index}",
                    }
                ]
            },
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": etag,
                "Idempotency-Key": f"race-{index}",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = sorted(pool.map(edit, range(4)))

    assert statuses == [201, 412, 412, 412]
    revisions = harness.client.get(path).json()["revisions"]
    assert [item["number"] for item in revisions] == [1, 2]
    winner = harness.client.get(f"{path}/{revisions[1]['id']}/docx-text-targets")
    assert winner.json()["targets"][0]["text"].startswith("Concurrent title ")


def test_concurrent_edits_publish_exactly_one_revision(
    harness: DocxEditHarness,
) -> None:
    exercise_concurrent_edits(harness)


def exercise_nested_markup_refusal(harness: DocxEditHarness) -> None:
    """Markup inside a text node is never offered and never silently dropped."""

    source = _SOURCE.read_bytes()
    document = _members(source)[_MAIN]
    assert document.count(b"<w:t>bold</w:t>") == 1
    nested = _repack(
        source, {_MAIN: document.replace(b"<w:t>bold</w:t>", b"<w:t>bo<w:b/>ld</w:t>")}
    )
    draft, base, etag = _docx_draft(harness, nested)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"

    targets = harness.client.get(
        f"{path}/{base['id']}/docx-text-targets?limit=500"
    ).json()["targets"]
    assert "word/document.xml#t2" not in {item["target"] for item in targets}
    assert {item["target"] for item in targets} >= {
        "word/document.xml#t1",
        "word/document.xml#t3",
    }
    rejected = harness.client.post(
        f"{path}/{base['id']}/docx-edits",
        json={
            "operations": [
                {
                    "op": "replace_text",
                    "target": "word/document.xml#t2",
                    "expected_text": "bold",
                    "text": "plain",
                }
            ]
        },
        headers={"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": "n"},
    )

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "COMPOSER_DOCX_EDIT_UNSUPPORTED_TARGET"
    assert harness.store.get_draft(harness.owner.id, draft.id).etag == etag
    assert len(harness.client.get(path).json()["revisions"]) == 1
    assert (
        harness.client.get(f"{path}/{base['id']}/artifacts/download").content == nested
    )


def test_markup_inside_text_nodes_is_refused_without_publication(
    harness: DocxEditHarness,
) -> None:
    exercise_nested_markup_refusal(harness)


def exercise_pptx_workflow(harness: DocxEditHarness) -> None:  # noqa: PLR0915 - one complete workflow
    """Slide and notes edits publish exact bytes, replay, refuse and restore."""

    client = harness.client
    deck = _DECK.read_bytes()
    draft, base, etag = _docx_draft(harness, deck, _PPTX)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"

    listed = client.get(f"{path}/{base['id']}/pptx-text-targets?limit=500")
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "private, no-store"
    page = listed.json()
    assert page["download_sha256"] == draft.source.sha256
    assert page["total"] == len(page["targets"]) == 13
    title, notes = (
        page["targets"][0],
        next(item for item in page["targets"] if item["notes"]),
    )
    assert title == {
        "target": "ppt/slides/slide1.xml#t0",
        "slide": 1,
        "notes": False,
        "paragraph": 0,
        "in_table": False,
        "text": "Deck Title Slide",
    }
    assert notes["target"] == "ppt/notesSlides/notesSlide1.xml#t0"
    assert any(item["in_table"] for item in page["targets"])
    assert client.get(f"{path}/{base['id']}/docx-text-targets").status_code == 422

    request = {
        "operations": [
            {
                "op": "replace_text",
                "target": title["target"],
                "expected_text": title["text"],
                "text": "Edited deck & title",
            },
            {
                "op": "replace_text",
                "target": notes["target"],
                "expected_text": notes["text"],
                "text": "Edited speaker note",
            },
        ]
    }
    headers = {"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": "p-1"}
    wrong_route = client.post(
        f"{path}/{base['id']}/docx-edits", json=request, headers=headers
    )
    assert wrong_route.status_code == 422
    stale = client.post(
        f"{path}/{base['id']}/pptx-edits",
        json={
            "operations": [{**request["operations"][0], "expected_text": "Stale title"}]
        },
        headers={**headers, "Idempotency-Key": "p-stale"},
    )
    assert stale.status_code == 422
    assert stale.json()["error"] == {
        "code": "COMPOSER_PPTX_EDIT_TARGET_CHANGED",
        "message": "The PPTX edit cannot be applied.",
        "paths": [title["target"]],
    }
    assert harness.store.get_draft(harness.owner.id, draft.id).etag == etag

    edited = client.post(
        f"{path}/{base['id']}/pptx-edits", json=request, headers=headers
    )
    assert edited.status_code == 201
    revision = edited.json()
    assert revision["operation"] == f"pptx_edit:{base['id']}"
    assert revision["provenance"] == "human:pptx_edit"
    assert revision["model_identity"] is None
    download = client.get(f"{path}/{revision['id']}/artifacts/download")
    preview = client.get(f"{path}/{revision['id']}/artifacts/preview")
    assert download.headers["Content-Type"] == _PPTX
    assert download.content == preview.content
    before, after = _members(deck), _members(download.content)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == {
        "ppt/slides/slide1.xml",
        "ppt/notesSlides/notesSlide1.xml",
    }
    diff = client.get(
        f"{path}/{revision['id']}/diff?from_revision_id={base['id']}"
    ).json()
    assert [(item["before_text"], item["after_text"]) for item in diff["changes"]] == [
        ("Slide 1: Deck Title Slide\n", "Slide 1: Edited deck & title\n"),
        (
            f"Slide 1 notes: {notes['text']}\n",
            "Slide 1 notes: Edited speaker note\n",
        ),
    ]
    reread = client.get(f"{path}/{revision['id']}/pptx-text-targets?limit=500")
    texts = {item["target"]: item["text"] for item in reread.json()["targets"]}
    assert texts[title["target"]] == "Edited deck & title"
    assert texts[notes["target"]] == "Edited speaker note"
    assert (
        client.post(
            f"{path}/{base['id']}/pptx-edits", json=request, headers=headers
        ).json()
        == revision
    )
    assert (
        client.post(
            f"{path}/{base['id']}/docx-edits", json=request, headers=headers
        ).status_code
        == 412
    )

    restored = client.post(
        f"{path}/{base['id']}/restore",
        headers={
            **headers,
            "If-Match": edited.headers["ETag"],
            "Idempotency-Key": "p-restore",
        },
    )
    assert restored.status_code == 201
    assert (
        client.get(f"{path}/{restored.json()['id']}/artifacts/download").content == deck
    )
    harness.authentication.authenticate.return_value = harness.other  # ty: ignore[unresolved-attribute]
    assert client.get(f"{path}/{base['id']}/pptx-text-targets").status_code == 404
    assert (
        client.post(
            f"{path}/{base['id']}/pptx-edits",
            json=request,
            headers={**headers, "Idempotency-Key": "p-foreign"},
        ).status_code
        == 404
    )


def test_pptx_slide_and_notes_edits_publish_exact_revisions(
    harness: DocxEditHarness,
) -> None:
    exercise_pptx_workflow(harness)
