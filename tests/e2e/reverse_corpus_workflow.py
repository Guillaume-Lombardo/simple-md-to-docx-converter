"""Exercise the approved reverse corpus through the authenticated final-image API."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from scripts.container.api_workflow_smoke import multipart
from tests.e2e.service_workflow import (
    ServiceClient,
    WorkflowFailure,
    create_user,
    decode_object,
    error_payload,
    expect,
)

CORPUS = Path("spikes/anydoc/corpus")
MANIFEST = CORPUS / "manifest.json"
_RESULT_LIMIT = 4_000_000
_POLL_SECONDS = 0.1
_JOB_TIMEOUT_SECONDS = 60
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ASSET_PATH = re.compile(r"assets/image-[0-9]{4,}\.png")
_UNSAFE_DOCX_TARGET = b'Target="../../fixture-src/sibling.odt"'
_SAFE_LINK = b"https://example.test/safe-url"
_DOCX_IMAGE = "word/media/image1.png"
_DOCX_RELATIONSHIPS = "word/_rels/document.xml.rels"
_CONTENT_TYPES = "[Content_Types].xml"
_REMOTE_SVG = Path("tests/corpus/security/remote-xlink.svg")


@dataclass(frozen=True, slots=True)
class CorpusCase:
    """One reviewed T69 family representative and its structural expectations."""

    label: str
    relative_path: str
    family: str
    detected_format: str
    minimum_headings: int = 0
    minimum_tables: int = 0
    minimum_bullets: int = 0
    minimum_links: int = 0
    expects_assets: bool = False
    repeat: bool = False

    @property
    def extension(self) -> str:
        return "." + self.relative_path.rsplit(".", 1)[1]

    @property
    def filename(self) -> str:
        return f"{self.label}{self.extension}"

    @property
    def api_detected_format(self) -> str | None:
        # CSV admission is extension plus bounded text validation; the parser records
        # the concrete format only in the successful result manifest.
        return None if self.family == "csv" else self.detected_format


SUCCESS_CASES = (
    CorpusCase(
        "word",
        "docx/text.docx",
        "word",
        "docx",
        minimum_headings=1,
        minimum_tables=1,
        minimum_bullets=1,
        minimum_links=1,
        expects_assets=True,
        repeat=True,
    ),
    CorpusCase(
        "powerpoint",
        "pptx/pres.pptx",
        "powerpoint",
        "pptx",
        minimum_tables=1,
        minimum_bullets=1,
    ),
    CorpusCase(
        "excel",
        "xlsx/sheet.xlsx",
        "excel",
        "xlsx",
        minimum_headings=1,
        minimum_tables=1,
    ),
    CorpusCase(
        "opendocument",
        "odt/text.odt",
        "opendocument",
        "odt",
        minimum_headings=1,
        minimum_tables=1,
        minimum_bullets=1,
        minimum_links=1,
        expects_assets=True,
    ),
    CorpusCase(
        "rtf",
        "rtf/text.rtf",
        "rtf",
        "rtf",
        minimum_headings=1,
        minimum_tables=1,
        minimum_bullets=1,
        minimum_links=1,
        expects_assets=True,
    ),
    CorpusCase(
        "epub",
        "epub/book.epub",
        "epub",
        "epub",
        minimum_headings=1,
        minimum_tables=1,
        minimum_links=1,
        expects_assets=True,
    ),
    CorpusCase(
        "csv",
        "csv/sheet.csv",
        "csv",
        "csv",
        minimum_tables=1,
        repeat=True,
    ),
    CorpusCase(
        "pdf",
        "pdf/text.pdf",
        "pdf",
        "pdf",
        minimum_headings=1,
    ),
)

FAILURE_CASES = (
    ("hostile-link", "docx/text.docx", "hostile.docx", "malformed"),
    (
        "encrypted",
        "malformed/encrypted--errors.odt",
        "encrypted.odt",
        "encrypted",
    ),
    (
        "resource-limit",
        "abuse/imagebomb--errors.docx",
        "resource.docx",
        "resource_limit",
    ),
    ("scanned-pdf", "pdf/handmade-scanned.pdf", "scanned.pdf", "needs_ocr"),
)


@dataclass(frozen=True, slots=True)
class AssetBoundaryCase:
    """One in-memory mutation that reaches the extracted-image boundary."""

    label: str
    payload: bytes
    extension: str = "png"
    media_type: str = "image/png"
    expected_code: str | None = "asset_invalid"


def _raster_bytes(format_name: str, *, animated: bool = False) -> bytes:
    output = io.BytesIO()
    first = Image.new("RGB", (8, 6), "#336699")
    if animated:
        second = Image.new("RGB", (8, 6), "#993366")
        first.save(
            output,
            format=format_name,
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
    else:
        first.save(output, format=format_name)
    return output.getvalue()


def asset_boundary_cases(original_png: bytes) -> tuple[AssetBoundaryCase, ...]:
    """Build the representative T70 image-boundary mutations."""

    try:
        hostile_svg = _REMOTE_SVG.read_bytes()
    except OSError as error:
        raise WorkflowFailure("reverse hostile SVG fixture is unavailable") from error
    return (
        AssetBoundaryCase("asset-non-image", b"not an image"),
        AssetBoundaryCase("asset-mismatched", _raster_bytes("JPEG")),
        AssetBoundaryCase("asset-polyglot", original_png + b"PK\x03\x04"),
        AssetBoundaryCase(
            "asset-animated", _raster_bytes("GIF", animated=True), "gif", "image/gif"
        ),
        AssetBoundaryCase(
            "asset-hostile-svg", hostile_svg, "svg", "image/svg+xml", None
        ),
    )


def load_reviewed_sources() -> dict[str, bytes]:
    """Load only manifest-bound corpus bytes and reject local fixture drift."""

    try:
        value: Any = json.loads(MANIFEST.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowFailure("reverse corpus manifest is invalid") from error
    records = value.get("files") if isinstance(value, dict) else None
    if not isinstance(records, list):
        raise WorkflowFailure("reverse corpus manifest has no file inventory")
    inventory = {
        record.get("path"): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    selected = {case.relative_path for case in SUCCESS_CASES} | {
        relative_path for _, relative_path, _, _ in FAILURE_CASES
    }
    sources: dict[str, bytes] = {}
    for relative_path in sorted(selected):
        record = inventory.get(relative_path)
        if not isinstance(record, dict):
            raise WorkflowFailure("reverse corpus representative is not inventoried")
        try:
            content = (CORPUS / relative_path).read_bytes()
        except OSError as error:
            raise WorkflowFailure(
                "reverse corpus representative is unavailable"
            ) from error
        digest = hashlib.sha256(content).hexdigest()
        if record.get("size") != len(content) or record.get("sha256") != digest:
            raise WorkflowFailure("reverse corpus representative identity differs")
        sources[relative_path] = content
    return sources


def safe_source(relative_path: str, source: bytes) -> bytes:
    """Normalize the corpus's deliberate unsafe local links without changing fixtures."""

    if relative_path == "rtf/text.rtf":
        unsafe = b"file:///anydoc/tests/fixture-src/sibling.odt"
        if source.count(unsafe) != 1:
            raise WorkflowFailure("reverse RTF link fixture differs")
        return source.replace(unsafe, _SAFE_LINK)
    member = {
        "docx/text.docx": "word/_rels/document.xml.rels",
        "odt/text.odt": "content.xml",
    }.get(relative_path)
    if member is None:
        return source
    unsafe = (
        _UNSAFE_DOCX_TARGET
        if relative_path == "docx/text.docx"
        else b"../../../fixture-src/sibling.odt"
    )
    output = io.BytesIO()
    replacements = 0
    try:
        with (
            zipfile.ZipFile(io.BytesIO(source)) as archive,
            zipfile.ZipFile(output, "w") as rebuilt,
        ):
            for info in archive.infolist():
                content = archive.read(info)
                if info.filename == member:
                    replacements = content.count(unsafe)
                    replacement = (
                        b'Target="' + _SAFE_LINK + b'"'
                        if relative_path == "docx/text.docx"
                        else _SAFE_LINK
                    )
                    content = content.replace(unsafe, replacement)
                rebuilt.writestr(info, content)
    except (OSError, zipfile.BadZipFile) as error:
        raise WorkflowFailure("reverse safe-link fixture is invalid") from error
    if replacements != 1:
        raise WorkflowFailure("reverse safe-link fixture differs")
    return output.getvalue()


def read_docx_image(source: bytes) -> bytes:
    """Read the reviewed DOCX image used to construct boundary mutations."""

    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            return archive.read(_DOCX_IMAGE)
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise WorkflowFailure("reverse DOCX image fixture differs") from error


def replace_docx_image(source: bytes, case: AssetBoundaryCase) -> bytes:
    """Replace one DOCX image and keep its relationship declaration coherent."""

    target = f"word/media/image1.{case.extension}"
    old_relationship = b"media/image1.png"
    new_relationship = f"media/image1.{case.extension}".encode()
    old_default = b'Extension="png" ContentType="image/png"'
    new_default = (
        f'Extension="{case.extension}" ContentType="{case.media_type}"'.encode()
    )
    old_override = b'PartName="/word/media/image1.png" ContentType="image/png"'
    new_override = (
        f'PartName="/word/media/image1.{case.extension}" '
        f'ContentType="{case.media_type}"'
    ).encode()
    output = io.BytesIO()
    counts = {"image": 0, "relationship": 0, "default": 0, "override": 0}
    try:
        with (
            zipfile.ZipFile(io.BytesIO(source)) as archive,
            zipfile.ZipFile(output, "w") as rebuilt,
        ):
            for original_info in archive.infolist():
                filename = (
                    target
                    if original_info.filename == _DOCX_IMAGE
                    else original_info.filename
                )
                info = zipfile.ZipInfo(filename, original_info.date_time)
                info.compress_type = original_info.compress_type
                info.comment = original_info.comment
                info.extra = original_info.extra
                info.create_system = original_info.create_system
                info.create_version = original_info.create_version
                info.extract_version = original_info.extract_version
                info.reserved = original_info.reserved
                info.flag_bits = original_info.flag_bits
                info.volume = original_info.volume
                info.internal_attr = original_info.internal_attr
                info.external_attr = original_info.external_attr
                content = archive.read(original_info)
                if original_info.filename == _DOCX_IMAGE:
                    counts["image"] += 1
                    content = case.payload
                elif original_info.filename == _DOCX_RELATIONSHIPS:
                    counts["relationship"] = content.count(old_relationship)
                    content = content.replace(old_relationship, new_relationship)
                elif original_info.filename == _CONTENT_TYPES:
                    counts["default"] = content.count(old_default)
                    counts["override"] = content.count(old_override)
                    content = content.replace(old_default, new_default).replace(
                        old_override, new_override
                    )
                rebuilt.writestr(info, content)
    except (OSError, zipfile.BadZipFile) as error:
        raise WorkflowFailure("reverse DOCX image fixture is invalid") from error
    if counts != {"image": 1, "relationship": 1, "default": 1, "override": 1}:
        raise WorkflowFailure("reverse DOCX image fixture differs")
    return output.getvalue()


def submit_and_wait(
    client: ServiceClient,
    *,
    label: str,
    filename: str,
    source: bytes,
    idempotency_key: str,
) -> dict[str, Any]:
    """Submit one source and return its terminal owner-visible snapshot."""

    body, content_type = multipart([], [("source", filename, source)])
    submitted = client.request(
        "POST",
        "/api/v1/reversions",
        body=body,
        content_type=content_type,
        mutate=True,
        headers={"Idempotency-Key": idempotency_key},
    )
    expect(submitted, 202, f"reverse {label} submission")
    job = decode_object(submitted, f"reverse {label} submission")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise WorkflowFailure(f"reverse {label} submission has no identity")
    deadline = time.monotonic() + _JOB_TIMEOUT_SECONDS
    while job.get("state") not in {"succeeded", "failed", "cancelled", "expired"}:
        if time.monotonic() >= deadline:
            raise WorkflowFailure(f"reverse {label} job did not finish")
        time.sleep(_POLL_SECONDS)
        response = client.request("GET", f"/api/v1/reversions/{job_id}")
        expect(response, 200, f"reverse {label} status")
        job = decode_object(response, f"reverse {label} status")
    return job


def _validate_markdown(case: CorpusCase, markdown: bytes) -> str:
    try:
        text = markdown.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise WorkflowFailure(f"reverse {case.label} Markdown is not UTF-8") from error
    if not text or "\x00" in text:
        raise WorkflowFailure(f"reverse {case.label} Markdown is empty or unsafe")
    lines = text.splitlines()
    counts = {
        "headings": sum(line.startswith("#") for line in lines),
        "tables": sum(line.startswith("|") for line in lines),
        "bullets": sum(line.startswith(("- ", "* ")) for line in lines),
        "links": text.count("]("),
    }
    expected = {
        "headings": case.minimum_headings,
        "tables": case.minimum_tables,
        "bullets": case.minimum_bullets,
        "links": case.minimum_links,
    }
    if any(counts[name] < minimum for name, minimum in expected.items()):
        raise WorkflowFailure(f"reverse {case.label} Markdown structure differs")
    return text


def _expected_manifest(
    case: CorpusCase, *, mode: str, asset_count: int, asset_bytes: int
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "engine": {"name": "firecrawl-anydoc", "version": "0.2.4"},
        "source": {"family": case.family, "detected_format": case.detected_format},
        "result": {
            "mode": mode,
            "asset_count": asset_count,
            "asset_bytes": asset_bytes,
            "unavailable_asset_count": 0,
        },
        "execution": {"local": True, "ocr": False, "hosted_fallback": False},
    }


def validate_package(case: CorpusCase, content: bytes) -> None:
    """Inspect the closed canonical ZIP, its Markdown, assets, and manifest."""

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if archive.comment or archive.testzip() is not None:
                raise WorkflowFailure(f"reverse {case.label} package integrity differs")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            assets = names[1:-1]
            if (
                not assets
                or names != ["document.md", *sorted(assets), "manifest.json"]
                or any(_ASSET_PATH.fullmatch(path) is None for path in assets)
                or any(
                    info.date_time != (1980, 1, 1, 0, 0, 0)
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.create_system != 3
                    or info.external_attr != 0o100644 << 16
                    for info in infos
                )
            ):
                raise WorkflowFailure(f"reverse {case.label} package layout differs")
            markdown = _validate_markdown(case, archive.read("document.md"))
            asset_content = [archive.read(path) for path in assets]
            if any(not value.startswith(_PNG_SIGNATURE) for value in asset_content):
                raise WorkflowFailure(f"reverse {case.label} asset signature differs")
            references = _ASSET_PATH.findall(markdown)
            if set(references) != set(assets) or any(
                references.count(path) < 1 for path in assets
            ):
                raise WorkflowFailure(f"reverse {case.label} asset references differ")
            manifest_bytes = archive.read("manifest.json")
    except zipfile.BadZipFile as error:
        raise WorkflowFailure(f"reverse {case.label} result is not a ZIP") from error
    try:
        manifest: Any = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowFailure(f"reverse {case.label} manifest is invalid") from error
    expected = _expected_manifest(
        case,
        mode="markdown_with_assets",
        asset_count=len(assets),
        asset_bytes=sum(map(len, asset_content)),
    )
    canonical = (
        json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()
    if manifest != expected or manifest_bytes != canonical:
        raise WorkflowFailure(f"reverse {case.label} manifest differs")


def _require_fields(
    label: str,
    stage: str,
    actual: Mapping[str, object],
    expected: Mapping[str, object],
) -> None:
    for field, expected_value in expected.items():
        if actual.get(field) != expected_value:
            raise WorkflowFailure(f"reverse {label} {stage} {field} differs")


def validate_success_result(
    client: ServiceClient, case: CorpusCase, job: dict[str, Any]
) -> bytes:
    """Validate terminal metadata, private headers, and downloaded result bytes."""

    expected_job = {
        "state": "succeeded",
        "source_family": case.family,
        "source_extension": case.extension,
        "detected_format": case.api_detected_format,
        "error_code": None,
        "error_message": None,
    }
    _require_fields(case.label, "terminal field", job, expected_job)
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise WorkflowFailure(f"reverse {case.label} terminal field id differs")
    result = client.request("GET", f"/api/v1/reversions/{job_id}/result")
    expect(result, 200, f"reverse {case.label} result")
    expected_headers = {
        "cache-control": "private, no-store",
        "x-content-type-options": "nosniff",
    }
    _require_fields(case.label, "result header", result.headers, expected_headers)
    if not result.body:
        raise WorkflowFailure(f"reverse {case.label} result body is empty")
    if len(result.body) > _RESULT_LIMIT:
        raise WorkflowFailure(f"reverse {case.label} result body exceeds test limit")
    if job.get("result_size") != len(result.body):
        raise WorkflowFailure(
            f"reverse {case.label} terminal field result_size differs"
        )
    media_type = result.headers.get("content-type", "")
    disposition = result.headers.get("content-disposition", "")
    if case.expects_assets:
        expected_result = {
            "content-type": "application/zip",
            "content-disposition": f'attachment; filename="{case.label}.zip"',
            "result_mode": "markdown_with_assets",
        }
    else:
        expected_result = {
            "content-type": "text/markdown; charset=utf-8",
            "content-disposition": f'attachment; filename="{case.label}.md"',
            "result_mode": "markdown",
        }
    _require_fields(
        case.label,
        "result field",
        {
            "content-type": media_type,
            "content-disposition": disposition,
            "result_mode": job.get("result_mode"),
        },
        expected_result,
    )
    if case.expects_assets:
        validate_package(case, result.body)
    else:
        _validate_markdown(case, result.body)
    return result.body


def validate_failure(
    client: ServiceClient, *, label: str, job: dict[str, Any], expected_code: str
) -> None:
    """Require one safe terminal category and an unavailable result."""

    expected_job = {
        "state": "failed",
        "error_code": expected_code,
        "result_mode": None,
        "result_size": None,
    }
    _require_fields(label, "terminal field", job, expected_job)
    if not isinstance(job.get("error_message"), str) or not job["error_message"]:
        raise WorkflowFailure(f"reverse {label} terminal field error_message differs")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise WorkflowFailure(f"reverse {label} terminal field id differs")
    result = client.request("GET", f"/api/v1/reversions/{job_id}/result")
    expect(result, 409, f"reverse {label} unavailable result")
    if error_payload(result).get("code") != "REVERSION_CONFLICT":
        raise WorkflowFailure(f"reverse {label} unavailable result code differs")


def exercise_corpus(base_url: str, profile: str) -> None:
    """Run every approved family and reviewed failure representative."""

    sources = load_reviewed_sources()
    admin = ServiceClient(base_url)
    admin.login("e2e-admin", "e2e-admin-password")
    username = f"t73-corpus-owner-{profile}"
    password = "T73-corpus-owner-fixture-password"  # noqa: S105 - E2E only
    create_user(admin, username, password)
    owner = ServiceClient(base_url)
    owner.login(username, password)

    for case in SUCCESS_CASES:
        source = safe_source(case.relative_path, sources[case.relative_path])
        results: list[bytes] = []
        executions = 2 if case.repeat else 1
        for execution in range(executions):
            job = submit_and_wait(
                owner,
                label=case.label,
                filename=case.filename,
                source=source,
                idempotency_key=f"t73-corpus-{profile}-{case.label}-{execution}",
            )
            results.append(validate_success_result(owner, case, job))
        if len(results) == 2 and results[0] != results[1]:
            raise WorkflowFailure(f"reverse {case.label} result is nondeterministic")

    for label, relative_path, filename, expected_code in FAILURE_CASES:
        job = submit_and_wait(
            owner,
            label=label,
            filename=filename,
            source=sources[relative_path],
            idempotency_key=f"t73-corpus-{profile}-{label}",
        )
        validate_failure(owner, label=label, job=job, expected_code=expected_code)

    word_case = SUCCESS_CASES[0]
    safe_docx = safe_source(word_case.relative_path, sources[word_case.relative_path])
    original_png = read_docx_image(safe_docx)
    for asset_case in asset_boundary_cases(original_png):
        job = submit_and_wait(
            owner,
            label=asset_case.label,
            filename=f"{asset_case.label}.docx",
            source=replace_docx_image(safe_docx, asset_case),
            idempotency_key=f"t73-corpus-{profile}-{asset_case.label}",
        )
        if asset_case.expected_code is None:
            validate_success_result(
                owner,
                CorpusCase(
                    asset_case.label,
                    word_case.relative_path,
                    word_case.family,
                    word_case.detected_format,
                    minimum_headings=word_case.minimum_headings,
                    minimum_tables=word_case.minimum_tables,
                    minimum_bullets=word_case.minimum_bullets,
                    minimum_links=word_case.minimum_links,
                    expects_assets=True,
                ),
                job,
            )
        else:
            validate_failure(
                owner,
                label=asset_case.label,
                job=job,
                expected_code=asset_case.expected_code,
            )

    print(f"T73 reverse corpus API workflow passed for {profile}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    exercise_corpus(arguments.base_url, arguments.profile)


if __name__ == "__main__":
    main()
