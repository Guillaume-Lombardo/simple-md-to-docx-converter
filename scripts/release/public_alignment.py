"""Fail closed when package and public container release identities drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PROJECT = "markweave"
REPOSITORY = "Guillaume-Lombardo/simple-md-to-docx-converter"
IMAGE_REPOSITORY = "ghcr.io/guillaume-lombardo/md-converter"
REGISTRY_PATH = "guillaume-lombardo/md-converter"
FRONTEND_REGISTRY_PATH = "guillaume-lombardo/md-converter-web"
PYPI_URL = f"https://pypi.org/pypi/{PROJECT}/json"
GITHUB_API = f"https://api.github.com/repos/{REPOSITORY}"
GITHUB_RELEASES = f"https://github.com/{REPOSITORY}/releases/download"
GHCR_TOKEN_URL = (
    f"https://ghcr.io/token?service=ghcr.io&scope=repository:{REGISTRY_PATH}:pull"
)
SKIPPED_CONTAINER_DEPLOYMENT_VERSION = "0.5.2"
SKIPPED_CONTAINER_PUBLIC_VERSION = "0.6.0"
SKIPPED_CONTAINER_CONTINUATION_VERSION = "0.6.1"
PENDING_EVENTS = frozenset({"pull_request", "merge_group", "push"})
KNOWN_EVENTS = PENDING_EVENTS | frozenset({"release", "workflow_dispatch", "schedule"})
FULL_SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE = re.compile(
    rf"{re.escape(IMAGE_REPOSITORY)}:(?P<tag>[0-9A-Za-z][0-9A-Za-z._-]*)"
    r"@(?P<digest>sha256:[0-9a-f]{64})"
)
ZERO_SHA = "0" * 40
MAX_JSON_BYTES = 1_048_576
MAX_RECEIPT_BYTES = 16_384
MAX_TOKEN_BYTES = 16_384
HTTP_TIMEOUT_SECONDS = 10.0
HTTP_OK = 200
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
OCI_SCHEMA_VERSION = 2


class AlignmentError(ValueError):
    """The repository and public release surfaces are not safely aligned."""


@dataclass(frozen=True)
class HttpResponse:
    """One bounded HTTP response used by the injectable public client."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    """Minimal network boundary for deterministic tests."""

    def request(self, url: str, *, headers: Mapping[str, str]) -> HttpResponse:
        """Return one response without following redirects."""


class _TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # noqa: PLR0913, PLR0917 - required stdlib override
        self, req, fp, code, msg, headers, newurl
    ):
        origin = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(newurl)
        if (origin.scheme, origin.hostname) != ("https", "github.com") or (
            target.scheme,
            target.hostname,
        ) != ("https", "release-assets.githubusercontent.com"):
            raise AlignmentError("public endpoint returned an untrusted redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrlLibTransport:
    """Bounded HTTPS-only transport for the fixed public endpoints."""

    def __init__(self, *, maximum_bytes: int = MAX_JSON_BYTES) -> None:
        self._maximum_bytes = maximum_bytes
        self._opener = urllib.request.build_opener(_TrustedRedirect)

    def request(self, url: str, *, headers: Mapping[str, str]) -> HttpResponse:
        if not url.startswith("https://"):
            raise AlignmentError("public release checks require HTTPS")
        request = urllib.request.Request(  # noqa: S310 - HTTPS checked above
            url, headers=dict(headers)
        )
        try:
            with self._opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read(self._maximum_bytes + 1)
                if len(body) > self._maximum_bytes:
                    raise AlignmentError(
                        "public endpoint response exceeds the size limit"
                    )
                return HttpResponse(
                    status=response.status,
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                    body=body,
                )
        except urllib.error.HTTPError as error:
            body = error.read(self._maximum_bytes + 1)
            if len(body) > self._maximum_bytes:
                raise AlignmentError(
                    "public endpoint response exceeds the size limit"
                ) from error
            return HttpResponse(
                status=error.code,
                headers={
                    key.lower(): value for key, value in (error.headers or {}).items()
                },
                body=body,
            )
        except (OSError, urllib.error.URLError) as error:
            raise AlignmentError(f"public endpoint request failed: {url}") from error


@dataclass(frozen=True)
class ComposeIdentity:
    """The exact public image selected by Compose."""

    version: str
    digest: str


@dataclass(frozen=True)
class BaseIdentity:
    """The exact repository base for one pending release transition."""

    version: str
    source_sha: str


def _canonical_final_version(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise AlignmentError(f"{label} must be a canonical final version string")
    try:
        parsed = Version(value)
    except InvalidVersion as error:
        raise AlignmentError(f"{label} is not valid PEP 440") from error
    if (
        value != str(parsed)
        or parsed.is_prerelease
        or parsed.is_devrelease
        or parsed.local is not None
        or parsed.epoch != 0
    ):
        raise AlignmentError(f"{label} must be a canonical final public version")
    return value


def parse_project_version(document: bytes, *, label: str = "pyproject.toml") -> str:
    """Parse the authoritative project version from one bounded TOML document."""
    try:
        value = tomllib.loads(document.decode("utf-8"))["project"]["version"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise AlignmentError(f"{label} has no valid project.version") from error
    return _canonical_final_version(value, label=f"{label} project.version")


def parse_compose_identity(document: str) -> ComposeIdentity:
    """Parse only the public image of the exact Compose markweave service."""
    try:
        compose = yaml.safe_load(document)
        image = compose["services"]["markweave"]["image"]
    except (yaml.YAMLError, KeyError, TypeError) as error:
        raise AlignmentError(
            "compose.yaml has no valid services.markweave.image"
        ) from error
    if not isinstance(image, str) or (match := IMAGE.fullmatch(image)) is None:
        raise AlignmentError(
            "Compose markweave image must use the trusted repository, a version tag, "
            "and an immutable sha256 digest"
        )
    version = _canonical_final_version(match.group("tag"), label="Compose image tag")
    return ComposeIdentity(version=version, digest=match.group("digest"))


def _json_response(
    transport: Transport,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    maximum_bytes: int = MAX_JSON_BYTES,
) -> dict[str, Any]:
    response = transport.request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
    )
    if response.status != HTTP_OK or len(response.body) > maximum_bytes:
        raise AlignmentError(f"public endpoint returned an invalid response: {url}")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlignmentError(f"public endpoint returned invalid JSON: {url}") from error
    if not isinstance(value, dict):
        raise AlignmentError(f"public endpoint JSON must be an object: {url}")
    return value


def _pypi_version(transport: Transport) -> str:
    document = _json_response(transport, PYPI_URL)
    info = document.get("info")
    if not isinstance(info, dict):
        raise AlignmentError("PyPI response has no info object")
    return _canonical_final_version(info.get("version"), label="latest PyPI version")


def _release_identity(transport: Transport, *, version: str) -> tuple[str, list[Any]]:
    tag = f"v{version}"
    release_url = f"{GITHUB_API}/releases/tags/{tag}"
    release = _json_response(transport, release_url)
    source_sha = release.get("target_commitish")
    if (
        release.get("tag_name") != tag
        or not isinstance(source_sha, str)
        or FULL_SHA.fullmatch(source_sha) is None
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(release.get("published_at"), str)
    ):
        raise AlignmentError(
            "GitHub Release identity does not match the public version"
        )

    ref = _json_response(transport, f"{GITHUB_API}/git/ref/tags/{tag}")
    ref_object = ref.get("object")
    if not isinstance(ref_object, dict) or (
        ref_object.get("type"),
        ref_object.get("sha"),
    ) != ("commit", source_sha):
        raise AlignmentError("GitHub tag does not match the published Release source")

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise AlignmentError("GitHub Release assets are invalid")
    return source_sha, assets


def _release_receipt(
    transport: Transport, *, version: str, expected_digest: str
) -> None:
    source_sha, assets = _release_identity(transport, version=version)
    tag = f"v{version}"
    receipt_url = f"{GITHUB_RELEASES}/{tag}/registry-publication.json"
    receipts = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == "registry-publication.json"
    ]
    if len(receipts) != 1 or receipts[0].get("browser_download_url") != receipt_url:
        raise AlignmentError("GitHub Release has no unique trusted publication receipt")
    receipt = _json_response(transport, receipt_url, maximum_bytes=MAX_RECEIPT_BYTES)
    archive_digest = receipt.get("oci_archive_manifest_digest")
    if (
        set(receipt)
        != {
            "oci_archive_manifest_digest",
            "registry_manifest_digest",
            "source_sha",
            "version",
        }
        or not isinstance(archive_digest, str)
        or DIGEST.fullmatch(archive_digest) is None
    ):
        raise AlignmentError("GitHub publication receipt has an invalid shape")
    if (
        receipt.get("registry_manifest_digest"),
        receipt.get("source_sha"),
        receipt.get("version"),
    ) != (expected_digest, source_sha, version):
        raise AlignmentError(
            "GitHub publication receipt does not match the public release"
        )


def _ghcr_manifest_response(
    transport: Transport, *, repository_path: str, version: str
) -> HttpResponse:
    token_url = (
        f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository_path}:pull"
    )
    token_document = _json_response(transport, token_url)
    token = token_document.get("token", token_document.get("access_token"))
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
        raise AlignmentError("GHCR returned no bounded anonymous pull token")
    return transport.request(
        f"https://ghcr.io/v2/{repository_path}/manifests/{version}",
        headers={
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
            "Authorization": f"Bearer {token}",
        },
    )


def _ghcr_tag_is_publicly_absent(
    transport: Transport, *, repository_path: str, version: str
) -> bool:
    token_url = (
        f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository_path}:pull"
    )
    token_response = transport.request(
        token_url, headers={"Accept": "application/json"}
    )
    if token_response.status == HTTP_FORBIDDEN:
        try:
            denial = json.loads(token_response.body)
        except UnicodeDecodeError, json.JSONDecodeError:
            return False
        errors = denial.get("errors") if isinstance(denial, dict) else None
        return (
            isinstance(errors, list)
            and len(errors) == 1
            and isinstance(errors[0], dict)
            and errors[0].get("code") == "DENIED"
        )
    if token_response.status != HTTP_OK or len(token_response.body) > MAX_JSON_BYTES:
        raise AlignmentError("GHCR returned an invalid anonymous token response")
    try:
        token_document = json.loads(token_response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlignmentError(
            "GHCR returned an invalid anonymous token response"
        ) from error
    if not isinstance(token_document, dict):
        raise AlignmentError("GHCR returned an invalid anonymous token response")
    token = token_document.get("token", token_document.get("access_token"))
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
        raise AlignmentError("GHCR returned no bounded anonymous pull token")
    response = transport.request(
        f"https://ghcr.io/v2/{repository_path}/manifests/{version}",
        headers={
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
            "Authorization": f"Bearer {token}",
        },
    )
    return response.status == HTTP_NOT_FOUND


def _ghcr_digest(transport: Transport, *, version: str) -> str:
    response = _ghcr_manifest_response(
        transport, repository_path=REGISTRY_PATH, version=version
    )
    headers = {key.lower(): value for key, value in response.headers.items()}
    digest = headers.get("docker-content-digest")
    if (
        response.status != HTTP_OK
        or not isinstance(digest, str)
        or DIGEST.fullmatch(digest) is None
    ):
        raise AlignmentError("GHCR manifest response has no immutable digest")
    if f"sha256:{hashlib.sha256(response.body).hexdigest()}" != digest:
        raise AlignmentError("GHCR manifest bytes do not match its public digest")
    try:
        manifest = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlignmentError("GHCR manifest is invalid JSON") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != OCI_SCHEMA_VERSION
    ):
        raise AlignmentError("GHCR manifest has an invalid shape")
    return digest


def _require_skipped_container_release(
    transport: Transport,
    *,
    project_version: str,
    compose_version: str,
    base: BaseIdentity | None,
) -> None:
    expected = (
        SKIPPED_CONTAINER_CONTINUATION_VERSION,
        SKIPPED_CONTAINER_DEPLOYMENT_VERSION,
        SKIPPED_CONTAINER_PUBLIC_VERSION,
    )
    if (project_version, compose_version, base.version if base else None) != expected:
        raise AlignmentError(
            "repository state is not an exact pending version transition"
        )
    if base is None or FULL_SHA.fullmatch(base.source_sha) is None:
        raise AlignmentError(
            "skipped-container continuation requires its release source"
        )
    source_sha, assets = _release_identity(
        transport, version=SKIPPED_CONTAINER_PUBLIC_VERSION
    )
    if source_sha != base.source_sha:
        raise AlignmentError(
            "skipped-container release does not match the exact base source"
        )
    if any(
        isinstance(asset, dict) and asset.get("name") == "registry-publication.json"
        for asset in assets
    ):
        raise AlignmentError(
            "skipped-container release already has a publication receipt"
        )
    for repository_path in (REGISTRY_PATH, FRONTEND_REGISTRY_PATH):
        if not _ghcr_tag_is_publicly_absent(
            transport,
            repository_path=repository_path,
            version=SKIPPED_CONTAINER_PUBLIC_VERSION,
        ):
            raise AlignmentError("skipped-container image tag is not publicly absent")


def _git_output(arguments: Sequence[str]) -> bytes:
    try:
        return subprocess.run(  # noqa: S603 - fixed executable and bounded arguments
            ["/usr/bin/git", *arguments], check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise AlignmentError("cannot inspect the exact Git transition") from error


def _base_identity(
    repository: Path, *, base: str, head: str, current_version: str
) -> BaseIdentity:
    if (
        FULL_SHA.fullmatch(base) is None
        or base == ZERO_SHA
        or FULL_SHA.fullmatch(head) is None
        or head == ZERO_SHA
    ):
        raise AlignmentError(
            "pending transitions require nonzero full base and head SHAs"
        )
    previous = _git_output(("-C", str(repository), "show", f"{base}:pyproject.toml"))
    reviewed_head = _git_output(
        ("-C", str(repository), "show", f"{head}:pyproject.toml")
    )
    if (
        parse_project_version(reviewed_head, label="head pyproject.toml")
        != current_version
    ):
        raise AlignmentError(
            "head project.version does not match the checked-out source"
        )
    return BaseIdentity(
        version=parse_project_version(previous, label="base pyproject.toml"),
        source_sha=base,
    )


def check_alignment(
    *,
    project_version: str,
    compose: ComposeIdentity,
    event_name: str,
    transport: Transport,
    base: BaseIdentity | None = None,
) -> str:
    """Verify normal alignment or the sole exact pending-release transition."""
    if event_name not in KNOWN_EVENTS:
        raise AlignmentError(f"unsupported GitHub event: {event_name}")
    pypi_version = _pypi_version(transport)
    if pypi_version != compose.version:
        if (
            event_name not in PENDING_EVENTS
            or pypi_version != SKIPPED_CONTAINER_PUBLIC_VERSION
        ):
            raise AlignmentError("latest PyPI version and Compose image tag differ")
        _require_skipped_container_release(
            transport,
            project_version=project_version,
            compose_version=compose.version,
            base=base,
        )
        return "pending-skipped-container"
    if project_version != compose.version:
        if event_name not in PENDING_EVENTS:
            raise AlignmentError("this event requires fully published alignment")
        if (
            base is None
            or base.version != compose.version
            or Version(project_version) <= Version(base.version)
        ):
            raise AlignmentError(
                "repository state is not an exact pending version transition"
            )
        state = "pending"
    else:
        if base is not None:
            raise AlignmentError("base version is only valid for a pending transition")
        state = "aligned"
    _release_receipt(transport, version=compose.version, expected_digest=compose.digest)
    if _ghcr_digest(transport, version=compose.version) != compose.digest:
        raise AlignmentError("public GHCR digest and Compose digest differ")
    return state


def _arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--event-name", required=True, choices=sorted(KNOWN_EVENTS))
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Check the current repository against fixed public release surfaces."""
    options = _arguments(arguments)
    try:
        project_version = parse_project_version(
            (options.repository / "pyproject.toml").read_bytes()
        )
        compose = parse_compose_identity(
            (options.repository / "compose.yaml").read_text(encoding="utf-8")
        )
        base = None
        if project_version != compose.version:
            if options.event_name not in PENDING_EVENTS:
                raise AlignmentError("this event requires fully published alignment")
            base = _base_identity(
                options.repository,
                base=options.base,
                head=options.head,
                current_version=project_version,
            )
        state = check_alignment(
            project_version=project_version,
            compose=compose,
            event_name=options.event_name,
            transport=UrlLibTransport(),
            base=base,
        )
    except (AlignmentError, OSError) as error:
        print(f"public release alignment failed: {error}", file=sys.stderr)
        return 1
    print(f"public release alignment: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
