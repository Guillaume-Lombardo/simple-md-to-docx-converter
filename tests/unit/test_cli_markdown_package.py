"""Unit coverage for client-side Markdown and local-image packaging (T104)."""

from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from markweave import conversion as conversion_package
from markweave.cli import markdown_package
from markweave.cli.commands import conversions
from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.main import main
from markweave.cli.markdown_package import (
    ENTRYPOINT,
    INVALID_ARCHIVE,
    PACKAGING_UNAVAILABLE,
    PackageError,
    build_package,
    load_image_references,
    package_markdown,
)
from markweave.conversion import image_references, validation
from markweave.conversion.errors import ConversionError
from markweave.conversion.image_references import (
    ESCAPING_IMAGE_PATH,
    INVALID_IMAGE_PATH,
    INVALID_METADATA,
    MISSING_IMAGE,
)
from markweave.conversion.images import SUPPORTED_IMAGE_SUFFIXES
from tests.markdown_image_references import FIXTURE, render

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\nclient-side-bytes"
JOB_ID = "11111111-1111-4111-8111-111111111111"
CORRELATION_ID = "22222222-2222-4222-8222-222222222222"
REFERENCES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_checked_in_parity_fixture_is_the_service_parser_output() -> None:
    """Regenerate with ``uv run python -m tests.markdown_image_references``."""
    assert FIXTURE.read_text(encoding="utf-8") == render()


def test_service_validation_uses_the_shared_reference_module() -> None:
    assert validation._MARKDOWN is image_references.MARKDOWN
    assert validation._METADATA_MARKDOWN is image_references.METADATA_MARKDOWN
    assert validation._is_remote_destination is image_references.is_remote_destination
    assert load_image_references() is image_references


@pytest.mark.parametrize(
    "case", REFERENCES["resolutions"], ids=lambda case: repr(case["destination"])
)
def test_service_resolution_matches_the_fixture(case: dict) -> None:
    destination = case["destination"]
    entrypoint = PurePosixPath(ENTRYPOINT)
    if case.get("remote"):
        assert validation._is_remote_destination(destination)
        return
    if "error" in case:
        with pytest.raises(ConversionError) as raised:
            validation._resolve_local_image(destination, entrypoint, frozenset())
        assert str(raised.value) == case["error"]
        return
    validation._resolve_local_image(
        destination, entrypoint, frozenset({PurePosixPath(case["path"])})
    )


def test_client_suffixes_match_the_service_image_formats() -> None:
    assert markdown_package.SUPPORTED_IMAGE_SUFFIXES == SUPPORTED_IMAGE_SUFFIXES


def test_reference_module_does_not_import_image_engines() -> None:
    script = (
        "import sys\n"
        "import markweave.cli.markdown_package\n"
        "assert 'markdown_it' not in sys.modules\n"
        "import markweave.conversion.image_references\n"
        "loaded = [m for m in ('PIL', 'cairosvg', 'tinycss2', 'fastapi') "
        "if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run(
        (sys.executable, "-I", "-c", script),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("markdown", "packaged"),
    [
        ("![logo][ref]\n\n[ref]:\n  assets/logo.png\n", "assets/logo.png"),
        ("![logo][ref]\n\n> [ref]: assets/logo.png\n", "assets/logo.png"),
        ("![logo](assets/&copy;.png)", "assets/©.png"),
    ],
    ids=["multiline-definition", "container-definition", "html-entity"],
)
def test_review_regressions_package_what_the_service_requires(
    tmp_path: Path, markdown: str, packaged: str
) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / packaged).write_bytes(PNG)

    package = package_markdown(tmp_path / "doc.md", markdown.encode())

    assert package is not None
    assert list(_members(package.content)) == [ENTRYPOINT, packaged]


def test_review_regression_list_indented_code_is_not_an_image(
    tmp_path: Path,
) -> None:
    markdown = b"- list\n\n      ![literal](missing.png)"

    assert package_markdown(tmp_path / "doc.md", markdown) is None


def test_invalid_metadata_reuses_the_service_message(tmp_path: Path) -> None:
    with pytest.raises(PackageError) as raised:
        package_markdown(tmp_path / "doc.md", b"---\n: [\n---\n![x](x.png)")

    assert raised.value.message == INVALID_METADATA
    assert raised.value.code == "validation"


def test_missing_packaging_extra_needs_guidance_only_for_image_syntax(
    tmp_path: Path, mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(conversion_package, "image_references")
    mocker.patch.dict(
        sys.modules,
        {"markdown_it": None, "markweave.conversion.image_references": None},
    )
    assert load_image_references() is None
    assert package_markdown(tmp_path / "doc.md", b"# Plain text only\n") is None

    with pytest.raises(PackageError) as raised:
        package_markdown(tmp_path / "doc.md", b"![x](x.png)")

    assert raised.value.code == "packaging_unavailable"
    assert raised.value.message == PACKAGING_UNAVAILABLE
    assert "markweave[packaging]" in PACKAGING_UNAVAILABLE


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.external_attr >> 16 == 0o100644
        return {info.filename: archive.read(info) for info in archive.infolist()}


def test_package_collects_only_referenced_images_deterministically(
    tmp_path: Path,
) -> None:
    (tmp_path / "assets" / "nested").mkdir(parents=True)
    (tmp_path / "assets" / "a.png").write_bytes(PNG)
    (tmp_path / "assets" / "nested" / "b.svg").write_bytes(b"<svg/>")
    (tmp_path / "unreferenced.png").write_bytes(b"unused")
    markdown = (
        b"# Title\n\n![a](assets/a.png)\n![again](./assets/a.png)\n"
        b"![b](assets/nested/b.svg)\n![remote](https://example.com/r.png)\n"
    )
    source = tmp_path / "report.md"

    first = package_markdown(source, markdown)
    second = package_markdown(source, markdown)

    assert first is not None and second is not None
    assert first.content == second.content
    assert first.image_count == 2
    members = _members(first.content)
    assert list(members) == [ENTRYPOINT, "assets/a.png", "assets/nested/b.svg"]
    assert members[ENTRYPOINT] == markdown
    assert members["assets/a.png"] == PNG


@pytest.mark.parametrize(
    "markdown",
    [b"# No images\n", b"![r](https://example.com/r.png)", b"\xff\xfe not utf-8"],
)
def test_markdown_without_local_images_is_uploaded_unchanged(
    tmp_path: Path, markdown: bytes
) -> None:
    assert package_markdown(tmp_path / "doc.md", markdown) is None


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        (b"![m](assets/missing.png)", MISSING_IMAGE),
        (b"![t](../outside.png)", ESCAPING_IMAGE_PATH),
        (b"![t](assets/../../outside.png)", ESCAPING_IMAGE_PATH),
        (b"![a](/etc/hostname.png)", INVALID_IMAGE_PATH),
        (b"![u](notes.txt)", MISSING_IMAGE),
        (b"![d](folder.png)", MISSING_IMAGE),
    ],
)
def test_package_rejects_missing_escaping_and_invalid_references(
    tmp_path: Path, markdown: bytes, message: str
) -> None:
    root = tmp_path / "root"
    (root / "folder.png").mkdir(parents=True)
    (root / "notes.txt").write_text("text", encoding="utf-8")
    (tmp_path / "outside.png").write_bytes(PNG)

    with pytest.raises(PackageError) as raised:
        package_markdown(root / "doc.md", markdown)

    assert raised.value.message == message


def test_package_rejects_symlinks_escaping_the_markdown_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.png").write_bytes(PNG)
    (tmp_path / "shared").mkdir()
    (root / "link.png").symlink_to(tmp_path / "secret.png")
    (root / "shared").symlink_to(tmp_path / "shared", target_is_directory=True)
    (tmp_path / "shared" / "x.png").write_bytes(PNG)

    for markdown in (b"![l](link.png)", b"![s](shared/x.png)"):
        with pytest.raises(PackageError) as raised:
            package_markdown(root / "doc.md", markdown)
        assert raised.value.message == ESCAPING_IMAGE_PATH


def test_package_follows_symlinks_that_stay_inside_the_markdown_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "x.png").write_bytes(PNG)
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)

    package = package_markdown(tmp_path / "doc.md", b"![x](alias/x.png)")

    assert package is not None
    assert _members(package.content)["alias/x.png"] == PNG


def test_package_rejects_case_colliding_archive_paths(tmp_path: Path) -> None:
    (tmp_path / "A.png").write_bytes(PNG)
    (tmp_path / "a.png").write_bytes(PNG)

    with pytest.raises(PackageError) as raised:
        package_markdown(tmp_path / "doc.md", b"![a](A.png) ![b](a.png)")

    assert raised.value.message == INVALID_ARCHIVE


def test_image_read_maps_loops_and_other_errors(tmp_path: Path, mocker) -> None:
    (tmp_path / "x.png").write_bytes(PNG)
    opener = mocker.patch.object(markdown_package.os, "open")
    for code, message in (
        (errno.ELOOP, ESCAPING_IMAGE_PATH),
        (errno.EACCES, MISSING_IMAGE),
    ):
        opener.side_effect = OSError(code, os.strerror(code))
        with pytest.raises(PackageError) as raised:
            package_markdown(tmp_path / "doc.md", b"![x](x.png)")
        assert raised.value.message == message


def test_build_package_places_markdown_at_the_approved_entrypoint() -> None:
    content = build_package(
        b"# T", {PurePosixPath("z.png"): b"z", PurePosixPath("a.png"): b"a"}
    )

    assert list(_members(content)) == [ENTRYPOINT, "a.png", "z.png"]


def _accepted() -> ConversionHttpResponse:
    job = {
        "id": JOB_ID,
        "owner_id": JOB_ID,
        "template_mode": "pandoc-default",
        "template_id": None,
        "template_version_id": None,
        "output": "docx",
        "component_versions": [["markweave", "0.7.3"]],
        "correlation_id": CORRELATION_ID,
        "state": "queued",
        "step": "queued",
        "progress": 0,
        "created_at": "2026-10-10T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
        "attempt": 0,
        "cancel_requested": False,
        "error_code": None,
        "error_message": None,
        "expires_at": None,
    }
    return ConversionHttpResponse(
        202, job, headers={"retry-after": "1", "x-correlation-id": CORRELATION_ID}
    )


@pytest.mark.parametrize(("images", "noun"), [(1, "image"), (2, "images")])
def test_convert_uploads_a_package_for_markdown_with_local_images(
    tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str], images: int, noun: str
) -> None:
    for index in range(images):
        (tmp_path / f"i{index}.png").write_bytes(PNG)
    source = tmp_path / "private-name.md"
    source.write_text(
        "".join(f"![i](i{index}.png)\n" for index in range(images)), encoding="utf-8"
    )
    client = mocker.Mock()
    client.submit.return_value = _accepted()
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(("convert", str(source))) == 0
    human = capsys.readouterr()
    assert f"with {images} packaged local {noun};" in human.out
    assert main(("--json", "convert", str(source))) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["packaged_images"] == images
    submitted = client.submit.call_args
    assert submitted.kwargs["source_kind"] == "zip"
    assert next(iter(_members(submitted.args[0]))) == ENTRYPOINT
    assert source.name not in json.dumps(result)


def test_convert_reports_stable_validation_errors_before_upload(
    tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "doc.md"
    source.write_text("![x](../escape.png)", encoding="utf-8")
    client_factory = mocker.patch.object(conversions, "_client")

    assert main(("--json", "convert", str(source))) == 1

    error = json.loads(capsys.readouterr().err)["error"]
    assert error == {"code": "validation", "message": ESCAPING_IMAGE_PATH}
    client_factory.assert_not_called()


def test_convert_can_upload_markdown_unchanged_without_packaging(
    tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "doc.md"
    source.write_text("![x](absent.png)", encoding="utf-8")
    client = mocker.Mock()
    client.submit.return_value = _accepted()
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(("--json", "convert", str(source), "--no-package")) == 0

    assert "packaged_images" not in json.loads(capsys.readouterr().out)
    assert client.submit.call_args.args[0] == b"![x](absent.png)"
    assert client.submit.call_args.kwargs["source_kind"] == "md"
