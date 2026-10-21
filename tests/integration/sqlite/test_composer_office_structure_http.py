"""Structural DOCX and PPTX edits over HTTP against real persistence and storage.

The scenario functions are shared with the PostgreSQL/S3 profile tests.
"""

import base64
import hashlib
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pytest_mock import MockerFixture

from markweave.malware import MalwareDetectedError, TrustingUploadScanner
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore
from tests.integration.sqlite.test_composer_docx_edits_http import (
    _DECK,
    _PPTX,
    DocxEditHarness,
    _docx_draft,
    _members,
    docx_edit_harness,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_MAIN = "word/document.xml"
_IMAGE = "word/media/image1.png"


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


def _jpeg() -> str:
    output = io.BytesIO()
    Image.new("RGB", (4, 3), (10, 200, 30)).save(output, format="JPEG")
    return base64.b64encode(output.getvalue()).decode()


def _headers(etag: str, key: str) -> dict[str, str]:
    return {"X-CSRF-Token": "csrf", "If-Match": etag, "Idempotency-Key": key}


def exercise_docx_structure(harness: DocxEditHarness) -> None:
    """Restyle paragraphs and replace an image, then replay and reject stale work."""

    client = harness.client
    draft, base, etag = _docx_draft(harness)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"
    read = client.get(f"{path}/{base['id']}/docx-structure")
    assert read.status_code == 200, read.text
    assert read.headers["Cache-Control"] == "private, no-store"
    structure = read.json()
    assert structure["format"] == "docx"
    assert structure["slides"] == []
    first, second = structure["paragraphs"][:2]
    assert (first["style"], first["text"]) == ("Heading1", "Fixture Document")
    assert second["style"] == "Normal"
    assert {"Heading2", "Normal"} <= {
        item["style_id"] for item in structure["paragraph_styles"]
    }
    image = next(item for item in structure["images"] if item["target"] == _IMAGE)
    request = {
        "operations": [
            {
                "op": "set_paragraph_style",
                "target": second["target"],
                "expected_style": "Normal",
                "style": "Heading2",
            },
            {
                "op": "replace_image",
                "target": _IMAGE,
                "expected_sha256": image["sha256"],
                "filename": "photo.jpg",
                "content_base64": _jpeg(),
            },
        ]
    }
    edited = client.post(
        f"{path}/{base['id']}/docx-structure-edits",
        json=request,
        headers=_headers(etag, "structure"),
    )
    assert edited.status_code == 201, edited.text
    revision = edited.json()
    assert revision["operation"] == f"docx_structure_edit:{base['id']}"
    assert revision["provenance"] == "human:docx_structure_edit"
    assert '"op":"replace_image"' in revision["approved_values"]
    download = client.get(f"{path}/{revision['id']}/artifacts/download").content
    preview = client.get(f"{path}/{revision['id']}/artifacts/preview").content
    assert download == preview
    before = _members(client.get(f"{path}/{base['id']}/artifacts/download").content)
    after = _members(download)
    assert {name for name in before if before[name] != after[name]} == {_MAIN, _IMAGE}
    with Image.open(io.BytesIO(after[_IMAGE])) as replaced:
        assert (replaced.format, replaced.size, replaced.info) == ("PNG", (4, 3), {})
    reread = client.get(f"{path}/{revision['id']}/docx-structure").json()
    assert reread["paragraphs"][1]["style"] == "Heading2"
    assert (
        next(item for item in reread["images"] if item["target"] == _IMAGE)["sha256"]
        == hashlib.sha256(after[_IMAGE]).hexdigest()
    )

    replay = client.post(
        f"{path}/{base['id']}/docx-structure-edits",
        json=request,
        headers=_headers(etag, "structure"),
    )
    assert replay.status_code == 201
    assert replay.json() == revision
    restyle = {**request["operations"][0], "expected_style": "Heading2"}
    stale = client.post(
        f"{path}/{revision['id']}/docx-structure-edits",
        json={"operations": [{**restyle, "style": "Normal"}]},
        headers=_headers(etag, "stale"),
    )
    assert stale.status_code == 412
    changed = client.post(
        f"{path}/{revision['id']}/docx-structure-edits",
        json={"operations": request["operations"][:1]},
        headers=_headers(edited.headers["ETag"], "changed"),
    )
    assert changed.status_code == 422
    assert changed.json()["error"] == {
        "code": "COMPOSER_DOCX_EDIT_TARGET_CHANGED",
        "message": "The DOCX edit cannot be applied.",
        "paths": [second["target"]],
    }


def test_docx_styles_and_images_publish_exact_revisions(
    harness: DocxEditHarness,
) -> None:
    exercise_docx_structure(harness)


def exercise_pptx_slide_order(harness: DocxEditHarness) -> None:
    """Reorder slides; the only changed part is the presentation slide list."""

    client = harness.client
    draft, base, etag = _docx_draft(harness, _DECK.read_bytes(), _PPTX)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions"
    structure = client.get(f"{path}/{base['id']}/pptx-structure").json()
    assert structure["paragraphs"] == []
    slides = structure["slides"]
    assert [item["title"] for item in slides] == ["Deck Title Slide", "Numbers Slide"]
    moved = client.post(
        f"{path}/{base['id']}/pptx-structure-edits",
        json={
            "operations": [
                {
                    "op": "move_slide",
                    "slide_id": slides[0]["slide_id"],
                    "expected_position": 1,
                    "position": 2,
                }
            ]
        },
        headers=_headers(etag, "slides"),
    )
    assert moved.status_code == 201, moved.text
    revision = moved.json()
    reread = client.get(f"{path}/{revision['id']}/pptx-structure").json()
    assert [item["title"] for item in reread["slides"]] == [
        "Numbers Slide",
        "Deck Title Slide",
    ]
    before = _members(_DECK.read_bytes())
    after = _members(client.get(f"{path}/{revision['id']}/artifacts/download").content)
    assert {name for name in before if before[name] != after[name]} == {
        "ppt/presentation.xml"
    }
    for operation, code in (
        (
            {
                "op": "set_paragraph_style",
                "target": "word/document.xml#p0",
                "expected_style": None,
                "style": "Normal",
            },
            "COMPOSER_PPTX_EDIT_UNSUPPORTED_OPERATION",
        ),
        (
            {"op": "move_slide", "slide_id": 1, "expected_position": 1, "position": 1},
            "COMPOSER_PPTX_EDIT_UNSUPPORTED_TARGET",
        ),
    ):
        refused = client.post(
            f"{path}/{revision['id']}/pptx-structure-edits",
            json={"operations": [operation]},
            headers=_headers(moved.headers["ETag"], code),
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == code


def test_pptx_slides_are_reordered_exactly(harness: DocxEditHarness) -> None:
    exercise_pptx_slide_order(harness)


def exercise_image_rejections(harness: DocxEditHarness, mocker: MockerFixture) -> None:
    """Infected, malformed, oversized or foreign images never publish."""

    client = harness.client
    draft, base, etag = _docx_draft(harness)
    path = f"/api/v1/composer/drafts/{draft.id}/revisions/{base['id']}"
    image = next(
        item
        for item in client.get(f"{path}/docx-structure").json()["images"]
        if item["target"] == _IMAGE
    )
    operation = {
        "op": "replace_image",
        "target": _IMAGE,
        "expected_sha256": image["sha256"],
        "filename": "photo.jpg",
        "content_base64": _jpeg(),
    }
    mocker.patch.object(
        TrustingUploadScanner, "scan", side_effect=MalwareDetectedError("infected")
    )
    infected = client.post(
        f"{path}/docx-structure-edits",
        json={"operations": [operation]},
        headers=_headers(etag, "infected"),
    )
    assert infected.status_code == 422
    assert infected.json()["error"]["code"] == "UPLOAD_MALWARE_DETECTED"
    mocker.stopall()
    for change, status, code in (
        ({"content_base64": "%%%%"}, 422, "COMPOSER_REQUEST_INVALID"),
        (
            {"content_base64": base64.b64encode(b"x" * 3000).decode()},
            413,
            "COMPOSER_REQUEST_TOO_LARGE",
        ),
        (
            {"content_base64": base64.b64encode(b"not an image").decode()},
            422,
            "COMPOSER_DOCX_EDIT_INVALID_IMAGE",
        ),
        ({"filename": "drawing.svg"}, 422, "COMPOSER_DOCX_EDIT_INVALID_IMAGE"),
        (
            {"target": "word/media/none.png"},
            422,
            "COMPOSER_DOCX_EDIT_UNSUPPORTED_TARGET",
        ),
        ({"expected_sha256": "0" * 64}, 422, "COMPOSER_DOCX_EDIT_TARGET_CHANGED"),
    ):
        refused = client.post(
            f"{path}/docx-structure-edits",
            json={"operations": [{**operation, **change}]},
            headers=_headers(etag, code),
        )
        assert refused.status_code == status, (change, refused.text)
        assert refused.json()["error"]["code"] == code
    for invalid in (
        {"operations": []},
        {"operations": [{**operation, "op": "delete_image"}]},
        {"operations": [{"op": "move_slide", "slide_id": 1}]},
    ):
        assert (
            client.post(
                f"{path}/docx-structure-edits",
                json=invalid,
                headers=_headers(etag, "invalid"),
            ).status_code
            == 422
        )
    assert harness.store.get_draft(harness.owner.id, draft.id).etag == etag
    authentication: Any = harness.authentication
    authentication.authenticate.return_value = harness.other
    try:
        assert client.get(f"{path}/docx-structure").status_code == 404
        assert (
            client.post(
                f"{path}/docx-structure-edits",
                json={"operations": [operation]},
                headers=_headers(etag, "foreign"),
            ).status_code
            == 404
        )
    finally:
        authentication.authenticate.return_value = harness.owner


def test_rejected_images_never_publish(
    harness: DocxEditHarness, mocker: MockerFixture
) -> None:
    exercise_image_rejections(harness, mocker)
