"""Tests for fail-closed public package and image alignment."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.release import public_alignment
from scripts.release.public_alignment import (
    AlignmentError,
    ComposeIdentity,
    HttpResponse,
    check_alignment,
    parse_compose_identity,
    parse_project_version,
)

SOURCE_SHA = "a" * 40
REGISTRY_MANIFEST = b'{"schemaVersion":2,"config":{}}'
REGISTRY_DIGEST = f"sha256:{hashlib.sha256(REGISTRY_MANIFEST).hexdigest()}"


@dataclass
class FakeTransport:
    """Return exact fixtures while recording the fixed endpoint contract."""

    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []

    def request(self, url: str, *, headers) -> HttpResponse:
        self.requests.append((url, dict(headers)))
        try:
            return self.responses[url]
        except KeyError as error:
            raise AssertionError(f"unexpected URL: {url}") from error


def _json_response(value: object) -> HttpResponse:
    return HttpResponse(
        200, {"content-type": "application/json"}, json.dumps(value).encode()
    )


def _public_transport(*, version: str = "0.4.0") -> FakeTransport:
    tag = f"v{version}"
    receipt_url = f"{public_alignment.GITHUB_RELEASES}/{tag}/registry-publication.json"
    return FakeTransport(
        {
            public_alignment.PYPI_URL: _json_response({"info": {"version": version}}),
            f"{public_alignment.GITHUB_API}/releases/tags/{tag}": _json_response(
                {
                    "tag_name": tag,
                    "target_commitish": SOURCE_SHA,
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-08-29T12:00:00Z",
                    "assets": [
                        {
                            "name": "registry-publication.json",
                            "browser_download_url": receipt_url,
                        }
                    ],
                }
            ),
            f"{public_alignment.GITHUB_API}/git/ref/tags/{tag}": _json_response(
                {"object": {"type": "commit", "sha": SOURCE_SHA}}
            ),
            receipt_url: _json_response(
                {
                    "oci_archive_manifest_digest": f"sha256:{'b' * 64}",
                    "registry_manifest_digest": REGISTRY_DIGEST,
                    "source_sha": SOURCE_SHA,
                    "version": version,
                }
            ),
            public_alignment.GHCR_TOKEN_URL: _json_response({"token": "pull-token"}),
            f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/{version}": HttpResponse(
                200,
                {"Docker-Content-Digest": REGISTRY_DIGEST},
                REGISTRY_MANIFEST,
            ),
        }
    )


@pytest.mark.unit
def test_accepts_fully_aligned_public_release() -> None:
    transport = _public_transport()

    state = check_alignment(
        project_version="0.4.0",
        compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
        event_name="schedule",
        transport=transport,
    )

    assert state == "aligned"
    assert [url for url, _headers in transport.requests] == [
        public_alignment.PYPI_URL,
        f"{public_alignment.GITHUB_API}/releases/tags/v0.4.0",
        f"{public_alignment.GITHUB_API}/git/ref/tags/v0.4.0",
        (f"{public_alignment.GITHUB_RELEASES}/v0.4.0/registry-publication.json"),
        public_alignment.GHCR_TOKEN_URL,
        f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/0.4.0",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("event_name", ["pull_request", "merge_group", "push"])
def test_accepts_only_exact_pending_release_transition(event_name: str) -> None:
    state = check_alignment(
        project_version="0.5.0",
        compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
        event_name=event_name,
        transport=_public_transport(),
        base_version="0.4.0",
    )

    assert state == "pending"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("event_name", "base_version", "message"),
    [
        ("schedule", None, "fully published"),
        ("workflow_dispatch", None, "fully published"),
        ("release", None, "fully published"),
        ("pull_request", "0.3.5", "exact pending"),
        ("pull_request", "0.5.0", "exact pending"),
    ],
)
def test_rejects_non_pending_drift(
    event_name: str, base_version: str | None, message: str
) -> None:
    with pytest.raises(AlignmentError, match=message):
        check_alignment(
            project_version="0.5.0",
            compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
            event_name=event_name,
            transport=_public_transport(),
            base_version=base_version,
        )


@pytest.mark.unit
def test_future_unchanged_revision_cannot_reuse_pending_exception() -> None:
    with pytest.raises(AlignmentError, match="exact pending"):
        check_alignment(
            project_version="0.5.0",
            compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
            event_name="pull_request",
            transport=_public_transport(),
            base_version="0.5.0",
        )


@pytest.mark.unit
def test_rejects_pypi_compose_drift_before_trusting_other_surfaces() -> None:
    transport = _public_transport(version="0.5.0")

    with pytest.raises(AlignmentError, match="PyPI version and Compose"):
        check_alignment(
            project_version="0.5.0",
            compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
            event_name="pull_request",
            transport=transport,
            base_version="0.4.0",
        )

    assert len(transport.requests) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("surface", "message"),
    [
        ("receipt", "receipt does not match"),
        ("tag", "tag does not match"),
        ("manifest", "manifest bytes"),
    ],
)
def test_rejects_public_identity_mismatches(surface: str, message: str) -> None:
    transport = _public_transport()
    if surface == "receipt":
        receipt_url = (
            f"{public_alignment.GITHUB_RELEASES}/v0.4.0/registry-publication.json"
        )
        transport.responses[receipt_url] = _json_response(
            {
                "oci_archive_manifest_digest": f"sha256:{'b' * 64}",
                "registry_manifest_digest": f"sha256:{'c' * 64}",
                "source_sha": SOURCE_SHA,
                "version": "0.4.0",
            }
        )
    elif surface == "tag":
        transport.responses[f"{public_alignment.GITHUB_API}/git/ref/tags/v0.4.0"] = (
            _json_response({"object": {"type": "commit", "sha": "d" * 40}})
        )
    else:
        manifest_url = (
            f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/0.4.0"
        )
        transport.responses[manifest_url] = HttpResponse(
            200,
            {"docker-content-digest": REGISTRY_DIGEST},
            b'{"schemaVersion":2}',
        )

    with pytest.raises(AlignmentError, match=message):
        check_alignment(
            project_version="0.4.0",
            compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
            event_name="push",
            transport=transport,
        )


@pytest.mark.unit
def test_parsers_require_canonical_version_and_exact_immutable_compose_ref() -> None:
    project = b'[project]\nversion = "0.5.0"\n'
    compose = f"services:\n  markweave:\n    image: {public_alignment.IMAGE_REPOSITORY}:0.5.0@{REGISTRY_DIGEST}\n"

    assert parse_project_version(project) == "0.5.0"
    assert parse_compose_identity(compose) == ComposeIdentity("0.5.0", REGISTRY_DIGEST)

    with pytest.raises(AlignmentError, match="canonical final"):
        parse_project_version(b'[project]\nversion = "0.5.0rc1"\n')
    with pytest.raises(AlignmentError, match="immutable"):
        parse_compose_identity(
            "services:\n  markweave:\n    image: ghcr.io/example/markweave:0.5.0\n"
        )


@pytest.mark.unit
def test_main_reads_exact_base_and_head_only_for_pending_transition(
    tmp_path: Path, mocker, capsys
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.5.0"\n', encoding="utf-8"
    )
    (tmp_path / "compose.yaml").write_text(
        f"services:\n  markweave:\n    image: {public_alignment.IMAGE_REPOSITORY}:0.4.0@{REGISTRY_DIGEST}\n",
        encoding="utf-8",
    )
    inspected = mocker.patch(
        "scripts.release.public_alignment._git_output",
        side_effect=[
            b'[project]\nversion = "0.4.0"\n',
            b'[project]\nversion = "0.5.0"\n',
        ],
    )
    mocker.patch(
        "scripts.release.public_alignment.UrlLibTransport",
        return_value=_public_transport(),
    )

    result = public_alignment.main(
        [
            "--repository",
            str(tmp_path),
            "--event-name",
            "pull_request",
            "--base",
            "f" * 40,
            "--head",
            "e" * 40,
        ]
    )

    assert result == 0
    assert "alignment: pending" in capsys.readouterr().out
    assert inspected.call_args_list == [
        mocker.call(("-C", str(tmp_path), "show", f"{'f' * 40}:pyproject.toml")),
        mocker.call(("-C", str(tmp_path), "show", f"{'e' * 40}:pyproject.toml")),
    ]


@pytest.mark.unit
def test_http_redirects_are_limited_to_the_fixed_github_release_cdn() -> None:
    handler = public_alignment._TrustedRedirect()
    request = urllib.request.Request("https://github.com/release/receipt")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://release-assets.githubusercontent.com/release/receipt?signature=value",
    )

    assert redirected.full_url.startswith(
        "https://release-assets.githubusercontent.com/release/receipt"
    )
    with pytest.raises(AlignmentError, match="untrusted redirect"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.invalid/receipt",
        )
