"""Exercise structured PowerPoint extraction through the authenticated final-image API."""

from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from scripts.container.api_workflow_smoke import multipart
from tests.e2e.service_workflow import (
    HttpResult,
    ServiceClient,
    WorkflowFailure,
    create_user,
    decode_object,
    error_payload,
    expect,
)

SOURCE = Path("spikes/anydoc/corpus/pptx/pres.pptx")
_SLIDE_ONE = "ppt/slides/slide1.xml"
_SLIDE_TWO = "ppt/slides/slide2.xml"
_SLIDE_ONE_RELS = "ppt/slides/_rels/slide1.xml.rels"
_NOTES_ONE = "ppt/notesSlides/notesSlide1.xml"
_MEDIA = "ppt/media/t83-edited.png"
_TITLE_ONE = "T83 edited first slide"
_TITLE_TWO = "T83 edited second slide"
_NOTE = "T83 edited presenter note"
_WARNING = "> Warning: unsupported drawing content is represented by this placeholder."
_PNG_SIZE = (4, 3)
_PNG_PIXELS = tuple(
    (x * 50 + 20, y * 70 + 10, (x + y) * 30 + 15)
    for y in range(_PNG_SIZE[1])
    for x in range(_PNG_SIZE[0])
)
_JOB_TIMEOUT_SECONDS = 90
_POLL_SECONDS = 0.1
_RESULT_LIMIT = 4_000_000
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class StructuredResult:
    content: bytes
    markdown: str
    asset: bytes


def _png(*, normalized: bool = False) -> bytes:
    image = Image.new("RGB", _PNG_SIZE)
    image.putdata(_PNG_PIXELS)
    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        compress_level=9 if normalized else 6,
        optimize=False,
    )
    return output.getvalue()


def _replace_once(content: bytes, old: bytes, new: bytes, label: str) -> bytes:
    if content.count(old) != 1:
        raise WorkflowFailure(f"structured PPTX {label} fixture differs")
    return content.replace(old, new)


def edited_presentation(source: bytes) -> bytes:
    """Apply realistic slide, note, picture, and connector edits in memory."""

    picture = b"""<p:pic><p:nvPicPr><p:cNvPr id="101" name="T83 picture" descr="Edited pixels"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rIdT83Image"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="800000" y="4200000"/><a:ext cx="1200000" cy="900000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>"""
    connector = b"""<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="102" name="T83 connector"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr><p:spPr><a:xfrm><a:off x="600000" y="600000"/><a:ext cx="1200000" cy="600000"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom><a:ln/></p:spPr></p:cxnSp>"""
    relationship = b'<Relationship Id="rIdT83Image" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/t83-edited.png"/>'
    output = io.BytesIO()
    seen: set[str] = set()
    try:
        with (
            zipfile.ZipFile(io.BytesIO(source)) as archive,
            zipfile.ZipFile(output, "w") as rebuilt,
        ):
            if archive.testzip() is not None or _MEDIA in archive.namelist():
                raise WorkflowFailure("structured PPTX source archive differs")
            for info in archive.infolist():
                content = archive.read(info)
                if info.filename == _SLIDE_ONE:
                    seen.add(_SLIDE_ONE)
                    content = _replace_once(
                        content,
                        b"Deck Title Slide",
                        _TITLE_ONE.encode(),
                        "first slide text",
                    )
                    content = _replace_once(
                        content, b"</p:spTree>", picture + b"</p:spTree>", "picture"
                    )
                elif info.filename == _SLIDE_TWO:
                    seen.add(_SLIDE_TWO)
                    content = _replace_once(
                        content,
                        b"Numbers Slide",
                        _TITLE_TWO.encode(),
                        "second slide text",
                    )
                    content = _replace_once(
                        content,
                        b"</p:spTree>",
                        connector + b"</p:spTree>",
                        "connector",
                    )
                elif info.filename == _NOTES_ONE:
                    seen.add(_NOTES_ONE)
                    content = _replace_once(
                        content,
                        b"Speaker note for the intro slide.",
                        _NOTE.encode(),
                        "presenter note",
                    )
                elif info.filename == _SLIDE_ONE_RELS:
                    seen.add(_SLIDE_ONE_RELS)
                    content = _replace_once(
                        content,
                        b"</Relationships>",
                        relationship + b"</Relationships>",
                        "image relationship",
                    )
                rebuilt.writestr(info, content)
            media = zipfile.ZipInfo(_MEDIA, _ZIP_TIMESTAMP)
            media.compress_type = zipfile.ZIP_STORED
            media.create_system = 3
            media.external_attr = 0o100644 << 16
            rebuilt.writestr(media, _png())
    except (OSError, zipfile.BadZipFile) as error:
        raise WorkflowFailure("structured PPTX source archive is invalid") from error
    if seen != {_SLIDE_ONE, _SLIDE_TWO, _SLIDE_ONE_RELS, _NOTES_ONE}:
        raise WorkflowFailure("structured PPTX source members differ")
    return output.getvalue()


def _require_error(result: HttpResult, status: int, code: str, operation: str) -> None:
    expect(result, status, operation)
    if error_payload(result).get("code") != code:
        raise WorkflowFailure(f"{operation}: stable error code differs")


def _submit(
    client: ServiceClient,
    source: bytes,
    *,
    label: str,
    profile: str,
    fields: list[tuple[str, str]],
) -> dict[str, Any]:
    body, content_type = multipart(fields, [("source", f"{label}.pptx", source)])
    response = client.request(
        "POST",
        "/api/v1/reversions",
        body=body,
        content_type=content_type,
        mutate=True,
        headers={"Idempotency-Key": f"t83-{profile}-{label}"},
    )
    expect(response, 202, f"structured PPTX {label} submission")
    job = decode_object(response, f"structured PPTX {label} submission")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise WorkflowFailure(f"structured PPTX {label} submission identity differs")
    deadline = time.monotonic() + _JOB_TIMEOUT_SECONDS
    while job.get("state") not in {"succeeded", "failed", "cancelled", "expired"}:
        if time.monotonic() >= deadline:
            raise WorkflowFailure(f"structured PPTX {label} job did not finish")
        time.sleep(_POLL_SECONDS)
        response = client.request("GET", f"/api/v1/reversions/{job_id}")
        expect(response, 200, f"structured PPTX {label} status")
        job = decode_object(response, f"structured PPTX {label} status")
    return job


def _download(client: ServiceClient, job: dict[str, Any], label: str) -> HttpResult:
    if job.get("state") != "succeeded":
        raise WorkflowFailure(f"structured PPTX {label} terminal state differs")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise WorkflowFailure(f"structured PPTX {label} terminal identity differs")
    result = client.request("GET", f"/api/v1/reversions/{job_id}/result")
    expect(result, 200, f"structured PPTX {label} result")
    if result.headers.get("cache-control") != "private, no-store":
        raise WorkflowFailure(f"structured PPTX {label} cache policy differs")
    if result.headers.get("x-content-type-options") != "nosniff":
        raise WorkflowFailure(f"structured PPTX {label} nosniff policy differs")
    if not 0 < len(result.body) <= _RESULT_LIMIT:
        raise WorkflowFailure(f"structured PPTX {label} result size differs")
    if job.get("result_size") != len(result.body):
        raise WorkflowFailure(f"structured PPTX {label} result metadata differs")
    return result


def _canonical_manifest(asset: bytes) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "engine": {"name": "firecrawl-anydoc", "version": "0.2.4"},
        "source": {"family": "powerpoint", "detected_format": "pptx"},
        "result": {
            "mode": "markdown_with_assets",
            "asset_count": 1,
            "asset_bytes": len(asset),
            "unavailable_asset_count": 0,
        },
        "execution": {"local": True, "ocr": False, "hosted_fallback": False},
        "extractor": "markweave-pptx-v1",
    }


def _read_structured_package(
    content: bytes, extraction: str
) -> tuple[bytes, bytes, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if archive.comment or archive.testzip() is not None:
                raise WorkflowFailure(
                    f"structured PPTX {extraction} ZIP integrity differs"
                )
            infos = archive.infolist()
            if [info.filename for info in infos] != [
                "document.md",
                "assets/image-0001.png",
                "manifest.json",
            ] or any(
                info.date_time != _ZIP_TIMESTAMP
                or info.compress_type != zipfile.ZIP_STORED
                or info.create_system != 3
                or info.external_attr != 0o100644 << 16
                for info in infos
            ):
                raise WorkflowFailure(
                    f"structured PPTX {extraction} ZIP layout differs"
                )
            markdown_bytes = archive.read("document.md")
            asset = archive.read("assets/image-0001.png")
            manifest_bytes = archive.read("manifest.json")
    except zipfile.BadZipFile as error:
        raise WorkflowFailure(
            f"structured PPTX {extraction} result is not a ZIP"
        ) from error
    return markdown_bytes, asset, manifest_bytes


def _validate_structured_markdown(markdown: str, extraction: str) -> None:
    if markdown.count("\n\n---\n\n") != 1:
        raise WorkflowFailure(f"structured PPTX {extraction} slide boundaries differ")
    if not (
        markdown.index(_TITLE_ONE)
        < markdown.index(_NOTE)
        < markdown.index("\n\n---\n\n")
        < markdown.index(_TITLE_TWO)
        < markdown.index(_WARNING)
    ):
        raise WorkflowFailure(f"structured PPTX {extraction} content order differs")
    if markdown.count("![Edited pixels](assets/image-0001.png)") != 1:
        raise WorkflowFailure(f"structured PPTX {extraction} image position differs")
    if extraction == "slides":
        if "::: notes\n" + _NOTE + "\n:::" not in markdown or markdown.startswith(
            "---\nmarp: true"
        ):
            raise WorkflowFailure("structured PPTX slides note syntax differs")
    elif extraction == "marp":
        if not markdown.startswith("---\nmarp: true\n---\n\n") or (
            "<!--\n" + _NOTE + "\n-->" not in markdown
        ):
            raise WorkflowFailure("structured PPTX marp note syntax differs")
    else:
        raise WorkflowFailure("structured PPTX extraction mode is invalid")


def _validate_structured_image(asset: bytes, extraction: str) -> None:
    if asset != _png(normalized=True):
        raise WorkflowFailure(f"structured PPTX {extraction} image bytes differ")
    try:
        with Image.open(io.BytesIO(asset)) as image:
            if image.format != "PNG" or image.mode != "RGB" or image.size != _PNG_SIZE:
                raise WorkflowFailure(
                    f"structured PPTX {extraction} image metadata differs"
                )
            if tuple(image.get_flattened_data()) != _PNG_PIXELS:
                raise WorkflowFailure(f"structured PPTX {extraction} pixels differ")
    except OSError as error:
        raise WorkflowFailure(
            f"structured PPTX {extraction} image is invalid"
        ) from error


def validate_structured_package(content: bytes, extraction: str) -> StructuredResult:
    """Inspect canonical package bytes and mode-specific slide syntax."""

    markdown_bytes, asset, manifest_bytes = _read_structured_package(
        content, extraction
    )
    try:
        markdown = markdown_bytes.decode("utf-8", errors="strict")
        manifest: Any = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowFailure(
            f"structured PPTX {extraction} text is invalid"
        ) from error
    expected_manifest = _canonical_manifest(asset)
    canonical = (
        json.dumps(expected_manifest, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()
    if manifest != expected_manifest or manifest_bytes != canonical:
        raise WorkflowFailure(f"structured PPTX {extraction} manifest differs")
    _validate_structured_markdown(markdown, extraction)
    _validate_structured_image(asset, extraction)
    return StructuredResult(content, markdown, asset)


def validate_structured_result(
    client: ServiceClient,
    job: dict[str, Any],
    extraction: str,
) -> StructuredResult:
    expected_options = {
        "extraction": extraction,
        "include_notes": True,
        "include_images": True,
    }
    if job.get("options") != expected_options:
        raise WorkflowFailure(f"structured PPTX {extraction} options differ")
    if (
        job.get("source_family") != "powerpoint"
        or job.get("source_extension") != ".pptx"
        or job.get("detected_format") != "pptx"
        or job.get("result_mode") != "markdown_with_assets"
        or job.get("error_code") is not None
        or job.get("error_message") is not None
    ):
        raise WorkflowFailure(f"structured PPTX {extraction} metadata differs")
    result = _download(client, job, extraction)
    if result.headers.get("content-type") != "application/zip":
        raise WorkflowFailure(f"structured PPTX {extraction} media type differs")
    return validate_structured_package(result.body, extraction)


def validate_default_anydoc(client: ServiceClient, job: dict[str, Any]) -> None:
    if job.get("options") != {
        "extraction": "anydoc",
        "include_notes": True,
        "include_images": True,
    }:
        raise WorkflowFailure("structured PPTX default options differ")
    result = _download(client, job, "default-anydoc")
    if job.get("result_mode") != "markdown_with_assets":
        raise WorkflowFailure("structured PPTX default result mode differs")
    try:
        with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
            markdown = archive.read("document.md").decode("utf-8", errors="strict")
            manifest: Any = json.loads(archive.read("manifest.json"))
    except (
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise WorkflowFailure("structured PPTX default package differs") from error
    if (
        manifest.get("source") != {"family": "powerpoint", "detected_format": "pptx"}
        or "extractor" in manifest
        or "\n\n---\n\n" in markdown
        or _TITLE_ONE not in markdown
        or _TITLE_TWO not in markdown
        or _NOTE not in markdown
    ):
        raise WorkflowFailure("structured PPTX default anydoc behavior differs")


def exercise_structured_pptx(base_url: str, profile: str) -> None:
    """Run structured modes, legacy default, option rejection, and owner denial."""

    try:
        original = SOURCE.read_bytes()
    except OSError as error:
        raise WorkflowFailure("structured PPTX corpus source is unavailable") from error
    source = edited_presentation(original)
    admin = ServiceClient(base_url)
    admin.login("e2e-admin", "e2e-admin-password")
    owner_username = f"t83-pptx-owner-{profile}"
    other_username = f"t83-pptx-other-{profile}"
    password = "T83-pptx-E2E-fixture-password"  # noqa: S105 - E2E only
    create_user(admin, owner_username, password)
    create_user(admin, other_username, password)
    owner = ServiceClient(base_url)
    owner.login(owner_username, password)
    other = ServiceClient(base_url)
    other.login(other_username, password)

    capabilities = owner.request("GET", "/api/v1/reversions/capabilities")
    expect(capabilities, 200, "structured PPTX capabilities")
    capability_body = decode_object(capabilities, "structured PPTX capabilities")
    if capability_body.get("extraction") != {
        "modes": ["anydoc", "slides", "marp"],
        "default_mode": "anydoc",
        "structured_extensions": [".pptx"],
        "include_notes_default": True,
        "include_images_default": True,
    }:
        raise WorkflowFailure("structured PPTX capabilities differ")

    results: dict[str, StructuredResult] = {}
    structured_job: dict[str, Any] | None = None
    for extraction in ("slides", "marp"):
        job = _submit(
            owner,
            source,
            label=extraction,
            profile=profile,
            fields=[
                ("extraction", extraction),
                ("include_notes", "true"),
                ("include_images", "true"),
            ],
        )
        results[extraction] = validate_structured_result(owner, job, extraction)
        structured_job = job
    if results["slides"].asset != results["marp"].asset:
        raise WorkflowFailure("structured PPTX mode image output differs")

    default_job = _submit(
        owner,
        source,
        label="default-anydoc",
        profile=profile,
        fields=[],
    )
    validate_default_anydoc(owner, default_job)

    invalid_body, invalid_type = multipart(
        [
            ("extraction", "anydoc"),
            ("include_notes", "false"),
            ("include_images", "true"),
        ],
        [("source", "invalid-options.pptx", source)],
    )
    invalid = owner.request(
        "POST",
        "/api/v1/reversions",
        body=invalid_body,
        content_type=invalid_type,
        mutate=True,
    )
    _require_error(
        invalid, 422, "REVERSION_REQUEST_INVALID", "structured PPTX invalid options"
    )

    if structured_job is None:  # pragma: no cover - closed non-empty mode set
        raise WorkflowFailure("structured PPTX job identity is unavailable")
    job_id = structured_job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise WorkflowFailure("structured PPTX job identity differs")
    _require_error(
        other.request("GET", f"/api/v1/reversions/{job_id}"),
        404,
        "REVERSION_NOT_FOUND",
        "structured PPTX cross-owner status",
    )
    _require_error(
        other.request("GET", f"/api/v1/reversions/{job_id}/result"),
        404,
        "REVERSION_NOT_FOUND",
        "structured PPTX cross-owner result",
    )
    print(f"T83 structured PPTX API workflow passed for {profile}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    exercise_structured_pptx(arguments.base_url, arguments.profile)


if __name__ == "__main__":
    main()
