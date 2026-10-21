"""CLI parity for bounded structural DOCX and PPTX edits on exact revisions."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.main import main
from tests.unit.test_cli_composer import DRAFT_ID, REVISION_ID
from tests.unit.test_cli_composer_office import _call, _private, remote

pytestmark = pytest.mark.unit

__all__ = ["remote"]

_CAPABILITIES = {"maximum_model_request_bytes": 4096, "maximum_upload_bytes": 64}
_REVISION = {"id": REVISION_ID, "number": 3, "operation": "docx_structure_edit:x"}
_STYLE = {
    "op": "set_paragraph_style",
    "target": "word/document.xml#p0",
    "expected_style": None,
    "style": "Heading1",
}
_MOVE = {"op": "move_slide", "slide_id": 257, "expected_position": 2, "position": 1}


def _restructure(path: Path, office_format: str = "docx") -> tuple[str, ...]:
    return (
        "composer",
        "office",
        "restructure",
        DRAFT_ID,
        REVISION_ID,
        "--format",
        office_format,
        "--operations-file",
        str(path),
        "--etag",
        '"draft-3"',
        "--idempotency-key",
        "structure-1",
    )


def test_structure_lists_paragraphs_slides_and_images(remote, capsys) -> None:
    remote.request.return_value = ConversionHttpResponse(
        200,
        {
            "format": "docx",
            "paragraphs": [
                {"target": "word/document.xml#p0", "style": None, "text": "Title"},
                {"target": "word/document.xml#p1", "style": "Quote", "text": "Body"},
            ],
            "paragraph_styles": [{"style_id": "Heading1", "name": "heading 1"}],
            "slides": [{"slide_id": 256, "position": 1, "title": "Opening"}],
            "images": [
                {
                    "target": "word/media/image1.png",
                    "media_type": "image/png",
                    "sha256": "a" * 64,
                }
            ],
        },
    )

    arguments = ("composer", "office", "structure", DRAFT_ID, REVISION_ID)
    assert main((*arguments, "--format", "docx")) == 0

    method, path, _kwargs = _call(remote, 0)
    assert (method, path) == (
        "GET",
        f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}/docx-structure",
    )
    output = capsys.readouterr().out
    assert "word/document.xml#p0\t(default)\tTitle" in output
    assert "word/document.xml#p1\tQuote\tBody" in output
    assert "slide 1\t256\tOpening" in output
    assert f"word/media/image1.png\timage/png\t{'a' * 64}" in output

    remote.request.return_value = ConversionHttpResponse(
        200, {"paragraphs": [], "slides": [], "images": []}
    )
    assert main((*arguments, "--format", "pptx")) == 0
    assert "No editable structure." in capsys.readouterr().out


def test_restructure_sends_styles_moves_and_a_scanned_image(
    remote, tmp_path, capsys
) -> None:
    image = tmp_path / "logo.png"
    image.write_bytes(b"\x89PNG-bytes")
    replace = {
        "op": "replace_image",
        "target": "word/media/image1.png",
        "expected_sha256": "b" * 64,
        "image_file": str(image),
    }
    path = _private(tmp_path / "structure.json", json.dumps([_STYLE, _MOVE, replace]))
    remote.request.side_effect = (
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(201, _REVISION),
    )

    assert main(_restructure(path)) == 0

    method, called, kwargs = _call(remote, 1)
    assert method == "POST"
    assert called.endswith(f"/revisions/{REVISION_ID}/docx-structure-edits")
    assert kwargs["csrf"] is True
    assert kwargs["headers"]["If-Match"] == '"draft-3"'
    assert kwargs["headers"]["Idempotency-Key"] == "structure-1"
    assert json.loads(kwargs["body"]) == {
        "operations": [
            _STYLE,
            _MOVE,
            {
                "op": "replace_image",
                "target": "word/media/image1.png",
                "expected_sha256": "b" * 64,
                "filename": "logo.png",
                "content_base64": base64.b64encode(b"\x89PNG-bytes").decode(),
            },
        ]
    }
    assert "Composer structure edits published." in capsys.readouterr().out


@pytest.mark.parametrize(
    "operations",
    [
        "not json",
        "[]",
        {"op": "move_slide"},
        [["move_slide"]],
        [{"op": ["move_slide"]}],
        [{"op": "unknown"}],
        [{**_MOVE, "extra": 1}],
        [{**_MOVE, "position": "1"}],
        [{**_MOVE, "slide_id": True}],
        [{**_STYLE, "target": 1}],
        [{**_STYLE, "style": None}],
        [{**_STYLE, "expected_style": 1}],
        [
            {
                "op": "replace_image",
                "target": "word/media/image1.png",
                "expected_sha256": 1,
                "image_file": "logo.png",
            }
        ],
        [
            {
                "op": "replace_image",
                "target": "word/media/image1.png",
                "expected_sha256": "b" * 64,
                "image_file": None,
            }
        ],
    ],
)
def test_restructure_rejects_malformed_operations_before_posting(
    remote, tmp_path, capsys, operations: object
) -> None:
    content = operations if isinstance(operations, str) else json.dumps(operations)
    path = _private(tmp_path / "structure.json", content)
    remote.request.return_value = ConversionHttpResponse(200, _CAPABILITIES)

    assert main(_restructure(path)) == 1
    assert remote.request.call_count == 1
    assert "structure operations are invalid" in capsys.readouterr().err


def test_restructure_refuses_unsafe_large_and_unsupported_inputs(
    remote, tmp_path, capsys
) -> None:
    remote.request.return_value = ConversionHttpResponse(200, _CAPABILITIES)
    shared = tmp_path / "shared.json"
    shared.write_text("[]", encoding="utf-8")
    shared.chmod(0o644)
    assert main(_restructure(shared)) == 1
    assert "Office structure files must be regular" in capsys.readouterr().err
    large = _private(tmp_path / "large.json", "x" * 5000)
    assert main(_restructure(large)) == 1
    assert "exceed the service limit" in capsys.readouterr().err

    for name, content, message in (
        ("logo.gif", b"GIF89a", "source type is unsupported"),
        ("huge.png", b"x" * 65, "upload is too large"),
    ):
        image = tmp_path / name
        image.write_bytes(content)
        replace = {
            "op": "replace_image",
            "target": "word/media/image1.png",
            "expected_sha256": "b" * 64,
            "image_file": str(image),
        }
        path = _private(tmp_path / f"{name}.json", json.dumps([replace]))
        assert main(_restructure(path)) == 1
        assert message in capsys.readouterr().err
    assert remote.request.call_count == 4
