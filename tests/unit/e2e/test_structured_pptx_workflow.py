"""Focused coverage for the final-image structured PowerPoint driver."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from PIL import Image

from tests.e2e import structured_pptx_workflow as workflow
from tests.e2e.service_workflow import HttpResult, WorkflowFailure

pytestmark = pytest.mark.unit


def _package(extraction: str, *, asset: bytes | None = None) -> bytes:
    normalized = asset if asset is not None else workflow._png(normalized=True)
    if extraction == "slides":
        notes = f"::: notes\n{workflow._NOTE}\n:::"
        prefix = ""
    else:
        notes = f"<!--\n{workflow._NOTE}\n-->"
        prefix = "---\nmarp: true\n---\n\n"
    markdown = (
        prefix
        + f"{workflow._TITLE_ONE}\n\n"
        + "![Edited pixels](assets/image-0001.png)\n\n"
        + notes
        + "\n\n---\n\n"
        + workflow._TITLE_TWO
        + "\n\n"
        + workflow._WARNING
        + "\n"
    ).encode()
    manifest = workflow._canonical_manifest(normalized)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in (
            ("document.md", markdown),
            ("assets/image-0001.png", normalized),
            (
                "manifest.json",
                (json.dumps(manifest, separators=(",", ":")) + "\n").encode(),
            ),
        ):
            info = zipfile.ZipInfo(name, workflow._ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def test_edited_presentation_preserves_fixture_and_adds_user_edits() -> None:
    original = workflow.SOURCE.read_bytes()

    edited = workflow.edited_presentation(original)

    assert edited != original
    assert workflow.SOURCE.read_bytes() == original
    with zipfile.ZipFile(io.BytesIO(edited)) as archive:
        assert archive.testzip() is None
        assert workflow._TITLE_ONE.encode() in archive.read(workflow._SLIDE_ONE)
        assert workflow._TITLE_TWO.encode() in archive.read(workflow._SLIDE_TWO)
        assert workflow._NOTE.encode() in archive.read(workflow._NOTES_ONE)
        assert b'r:embed="rIdT83Image"' in archive.read(workflow._SLIDE_ONE)
        assert b"<p:cxnSp>" in archive.read(workflow._SLIDE_TWO)
        assert b"../media/t83-edited.png" in archive.read(workflow._SLIDE_ONE_RELS)
        image = archive.read(workflow._MEDIA)
    with Image.open(io.BytesIO(image)) as opened:
        assert opened.size == workflow._PNG_SIZE
        assert tuple(opened.get_flattened_data()) == workflow._PNG_PIXELS


@pytest.mark.parametrize("extraction", ["slides", "marp"])
def test_structured_package_validation_checks_mode_syntax_and_exact_pixels(
    extraction: str,
) -> None:
    result = workflow.validate_structured_package(_package(extraction), extraction)

    assert workflow._TITLE_ONE in result.markdown
    assert result.asset == workflow._png(normalized=True)


def test_structured_package_rejects_noncanonical_image_bytes() -> None:
    with pytest.raises(WorkflowFailure, match="image bytes differ"):
        workflow.validate_structured_package(
            _package("slides", asset=workflow._png()), "slides"
        )


def test_stable_error_validation_rejects_unrelated_envelope() -> None:
    result = HttpResult(
        422,
        {},
        b'{"error":{"code":"OTHER","message":"safe"}}',
        (),
    )

    with pytest.raises(WorkflowFailure, match="stable error code differs"):
        workflow._require_error(
            result, 422, "REVERSION_REQUEST_INVALID", "structured PPTX invalid options"
        )


def test_parser_requires_profile_and_base_url() -> None:
    parsed = workflow.build_parser().parse_args(
        ["--base-url", "http://127.0.0.1:8080", "--profile", "standalone"]
    )

    assert parsed.base_url == "http://127.0.0.1:8080"
    assert parsed.profile == "standalone"
