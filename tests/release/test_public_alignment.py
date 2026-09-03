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
    BaseIdentity,
    ComposeIdentity,
    HttpResponse,
    RegistryCredentials,
    check_alignment,
    parse_compose_identity,
    parse_frontend_compose_identity,
    parse_project_version,
)

SOURCE_SHA = "a" * 40
REGISTRY_CREDENTIALS = RegistryCredentials("github-actions", "read-only-token")
REGISTRY_MANIFEST = b'{"schemaVersion":2,"config":{}}'
REGISTRY_DIGEST = f"sha256:{hashlib.sha256(REGISTRY_MANIFEST).hexdigest()}"


@dataclass
class FakeTransport:
    """Return exact fixtures while recording the fixed endpoint contract."""

    responses: dict[str, HttpResponse | list[HttpResponse]]

    def __post_init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []

    def request(self, url: str, *, headers) -> HttpResponse:
        self.requests.append((url, dict(headers)))
        try:
            response = self.responses[url]
            if isinstance(response, list):
                if not response:
                    raise AssertionError(f"exhausted responses for URL: {url}")
                return response.pop(0)
            return response
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


def _skipped_container_transport() -> FakeTransport:
    version = "0.6.0"
    tag = f"v{version}"
    backend_token = public_alignment.GHCR_TOKEN_URL
    frontend_token = (
        "https://ghcr.io/token?service=ghcr.io&scope=repository:"
        f"{public_alignment.FRONTEND_REGISTRY_PATH}:pull"
    )
    transport = _public_transport(version="0.5.2")
    transport.responses.update(
        {
            public_alignment.PYPI_URL: _json_response({"info": {"version": version}}),
            f"{public_alignment.GITHUB_API}/releases/tags/{tag}": _json_response(
                {
                    "tag_name": tag,
                    "target_commitish": SOURCE_SHA,
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-09-03T01:43:35Z",
                    "assets": [],
                }
            ),
            f"{public_alignment.GITHUB_API}/git/ref/tags/{tag}": _json_response(
                {"object": {"type": "commit", "sha": SOURCE_SHA}}
            ),
            backend_token: _json_response({"token": "backend-pull-token"}),
            f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/{version}": HttpResponse(
                404,
                {"content-type": "application/json"},
                b'{"errors":[{"code":"MANIFEST_UNKNOWN","message":"manifest unknown"}]}',
            ),
            frontend_token: [
                HttpResponse(
                    403,
                    {"content-type": "application/json"},
                    b'{"errors":[{"code":"DENIED","message":"requested access to the resource is denied"}]}',
                ),
                _json_response({"token": "frontend-pull-token"}),
            ],
            f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/{version}": HttpResponse(
                404,
                {"content-type": "application/json"},
                b'{"errors":[{"code":"MANIFEST_UNKNOWN","message":"manifest unknown"}]}',
            ),
        }
    )
    return transport


def _paired_transport() -> FakeTransport:
    version = "0.6.1"
    tag = f"v{version}"
    transport = _public_transport(version=version)
    release_url = f"{public_alignment.GITHUB_API}/releases/tags/{tag}"
    release_response = transport.responses[release_url]
    assert isinstance(release_response, HttpResponse)
    release = json.loads(release_response.body)
    release["assets"] = []
    transport.responses[release_url] = _json_response(release)
    legacy_receipt = (
        f"{public_alignment.GITHUB_RELEASES}/{tag}/registry-publication.json"
    )
    del transport.responses[legacy_receipt]
    for role in ("backend", "frontend"):
        name = f"{role}-registry-publication.json"
        url = f"{public_alignment.GITHUB_RELEASES}/{tag}/{name}"
        release["assets"].append({"name": name, "browser_download_url": url})
        transport.responses[url] = _json_response(
            {
                "oci_archive_manifest_digest": f"sha256:{'b' * 64}",
                "registry_manifest_digest": REGISTRY_DIGEST,
                "source_sha": SOURCE_SHA,
                "version": version,
            }
        )
    transport.responses[release_url] = _json_response(release)
    frontend_token = (
        "https://ghcr.io/token?service=ghcr.io&scope=repository:"
        f"{public_alignment.FRONTEND_REGISTRY_PATH}:pull"
    )
    transport.responses[frontend_token] = _json_response({"token": "frontend-token"})
    transport.responses[
        f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/{version}"
    ] = HttpResponse(200, {"Docker-Content-Digest": REGISTRY_DIGEST}, REGISTRY_MANIFEST)
    return transport


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
def test_accepts_paired_public_release_only_after_both_exact_receipts() -> None:
    transport = _paired_transport()
    identity = ComposeIdentity("0.6.1", REGISTRY_DIGEST)

    state = check_alignment(
        project_version="0.6.1",
        compose=identity,
        frontend=identity,
        event_name="schedule",
        transport=transport,
    )

    assert state == "aligned"
    requested = [url for url, _headers in transport.requests]
    assert (
        f"{public_alignment.GITHUB_RELEASES}/v0.6.1/backend-registry-publication.json"
        in requested
    )
    assert (
        f"{public_alignment.GITHUB_RELEASES}/v0.6.1/frontend-registry-publication.json"
        in requested
    )
    assert (
        f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/0.6.1"
        in requested
    )


@pytest.mark.unit
def test_paired_public_release_rejects_missing_or_mismatched_frontend() -> None:
    for frontend in (None, ComposeIdentity("0.6.2", REGISTRY_DIGEST)):
        with pytest.raises(
            AlignmentError, match="backend and frontend Compose versions"
        ):
            check_alignment(
                project_version="0.6.1",
                compose=ComposeIdentity("0.6.1", REGISTRY_DIGEST),
                frontend=frontend,
                event_name="schedule",
                transport=_paired_transport(),
            )


@pytest.mark.unit
@pytest.mark.parametrize("event_name", ["pull_request", "merge_group", "push"])
def test_accepts_only_exact_pending_release_transition(event_name: str) -> None:
    state = check_alignment(
        project_version="0.5.0",
        compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
        event_name=event_name,
        transport=_public_transport(),
        base=BaseIdentity("0.4.0", SOURCE_SHA),
    )

    assert state == "pending"


@pytest.mark.unit
@pytest.mark.parametrize("event_name", ["pull_request", "merge_group", "push"])
def test_accepts_exact_061_transition_after_skipped_060_containers(
    event_name: str,
) -> None:
    transport = _skipped_container_transport()

    state = check_alignment(
        project_version="0.6.1",
        compose=ComposeIdentity("0.5.2", REGISTRY_DIGEST),
        event_name=event_name,
        transport=transport,
        base=BaseIdentity("0.6.0", SOURCE_SHA),
        registry_credentials=REGISTRY_CREDENTIALS,
    )

    assert state == "pending-skipped-container"
    assert [url for url, _headers in transport.requests] == [
        public_alignment.PYPI_URL,
        f"{public_alignment.GITHUB_API}/releases/tags/v0.5.2",
        f"{public_alignment.GITHUB_API}/git/ref/tags/v0.5.2",
        f"{public_alignment.GITHUB_RELEASES}/v0.5.2/registry-publication.json",
        public_alignment.GHCR_TOKEN_URL,
        f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/0.5.2",
        f"{public_alignment.GITHUB_API}/releases/tags/v0.6.0",
        f"{public_alignment.GITHUB_API}/git/ref/tags/v0.6.0",
        public_alignment.GHCR_TOKEN_URL,
        f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/0.6.0",
        (
            "https://ghcr.io/token?service=ghcr.io&scope=repository:"
            f"{public_alignment.FRONTEND_REGISTRY_PATH}:pull"
        ),
        (
            "https://ghcr.io/token?service=ghcr.io&scope=repository:"
            f"{public_alignment.FRONTEND_REGISTRY_PATH}:pull"
        ),
        f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/0.6.0",
    ]
    frontend_token_requests = [
        headers
        for url, headers in transport.requests
        if url
        == (
            "https://ghcr.io/token?service=ghcr.io&scope=repository:"
            f"{public_alignment.FRONTEND_REGISTRY_PATH}:pull"
        )
    ]
    assert "Authorization" not in frontend_token_requests[0]
    assert frontend_token_requests[1]["Authorization"].startswith("Basic ")


@pytest.mark.unit
def test_skipped_container_transition_still_verifies_deployed_exact_bytes() -> None:
    with pytest.raises(AlignmentError, match="receipt does not match"):
        check_alignment(
            project_version="0.6.1",
            compose=ComposeIdentity("0.5.2", f"sha256:{'c' * 64}"),
            event_name="pull_request",
            transport=_skipped_container_transport(),
            base=BaseIdentity("0.6.0", SOURCE_SHA),
            registry_credentials=REGISTRY_CREDENTIALS,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("backend-present", "not proven publicly absent"),
        ("frontend-present", "not proven publicly absent"),
        ("frontend-auth-token-denied", "invalid pull token response"),
        ("backend-malformed-404", "absence response is invalid"),
        ("frontend-unrelated-404", "absence response is invalid"),
        ("frontend-oversized-404", "not proven publicly absent"),
        ("receipt-present", "already has a publication receipt"),
        ("wrong-source", "does not match the exact base source"),
    ],
)
def test_skipped_container_transition_rejects_any_inconsistent_public_surface(
    mutation: str, message: str
) -> None:
    transport = _skipped_container_transport()
    manifest = HttpResponse(
        200, {"docker-content-digest": REGISTRY_DIGEST}, REGISTRY_MANIFEST
    )
    if mutation == "backend-present":
        transport.responses[
            f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/0.6.0"
        ] = manifest
    elif mutation == "frontend-present":
        transport.responses[
            f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/0.6.0"
        ] = manifest
    elif mutation == "frontend-auth-token-denied":
        repository_path = public_alignment.FRONTEND_REGISTRY_PATH
        token_url = (
            "https://ghcr.io/token?service=ghcr.io&scope=repository:"
            f"{repository_path}:pull"
        )
        transport.responses[token_url] = [
            HttpResponse(
                403,
                {"content-type": "application/json"},
                b'{"errors":[{"code":"DENIED","message":"requested access to the resource is denied"}]}',
            ),
            HttpResponse(403, {}, b""),
        ]
    elif mutation == "backend-malformed-404":
        transport.responses[
            f"https://ghcr.io/v2/{public_alignment.REGISTRY_PATH}/manifests/0.6.0"
        ] = HttpResponse(404, {}, b"not-json")
    elif mutation == "frontend-unrelated-404":
        transport.responses[
            f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/0.6.0"
        ] = HttpResponse(
            404,
            {},
            b'{"errors":[{"code":"NAME_UNKNOWN","message":"repository name not known to registry"}]}',
        )
    elif mutation == "frontend-oversized-404":
        transport.responses[
            f"https://ghcr.io/v2/{public_alignment.FRONTEND_REGISTRY_PATH}/manifests/0.6.0"
        ] = HttpResponse(404, {}, b"x" * 4_097)
    elif mutation == "receipt-present":
        release_url = f"{public_alignment.GITHUB_API}/releases/tags/v0.6.0"
        release_response = transport.responses[release_url]
        assert isinstance(release_response, HttpResponse)
        release = json.loads(release_response.body)
        release["assets"] = [{"name": "registry-publication.json"}]
        transport.responses[release_url] = _json_response(release)

    with pytest.raises(AlignmentError, match=message):
        check_alignment(
            project_version="0.6.1",
            compose=ComposeIdentity("0.5.2", REGISTRY_DIGEST),
            event_name="pull_request",
            transport=transport,
            base=BaseIdentity(
                "0.6.0", "b" * 40 if mutation == "wrong-source" else SOURCE_SHA
            ),
            registry_credentials=REGISTRY_CREDENTIALS,
        )


@pytest.mark.unit
def test_backend_anonymous_denial_never_requests_credentials() -> None:
    transport = _skipped_container_transport()
    token_url = public_alignment.GHCR_TOKEN_URL
    transport.responses[token_url] = [
        _json_response({"token": "deployed-backend-token"}),
        HttpResponse(
            403,
            {"content-type": "application/json"},
            b'{"errors":[{"code":"DENIED","message":"requested access to the resource is denied"}]}',
        ),
        _json_response({"token": "must-not-be-requested"}),
    ]

    with pytest.raises(AlignmentError, match="backend anonymous access was denied"):
        check_alignment(
            project_version="0.6.1",
            compose=ComposeIdentity("0.5.2", REGISTRY_DIGEST),
            event_name="pull_request",
            transport=transport,
            base=BaseIdentity("0.6.0", SOURCE_SHA),
            registry_credentials=REGISTRY_CREDENTIALS,
        )

    backend_token_requests = [
        headers for url, headers in transport.requests if url == token_url
    ]
    assert len(backend_token_requests) == 2
    assert all("Authorization" not in headers for headers in backend_token_requests)


@pytest.mark.unit
def test_skipped_container_transition_rejects_missing_fallback_credentials() -> None:
    with pytest.raises(AlignmentError, match="credentials are unavailable"):
        check_alignment(
            project_version="0.6.1",
            compose=ComposeIdentity("0.5.2", REGISTRY_DIGEST),
            event_name="pull_request",
            transport=_skipped_container_transport(),
            base=BaseIdentity("0.6.0", SOURCE_SHA),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("project_version", "compose_version", "base_version"),
    [
        ("0.6.2", "0.5.2", "0.6.0"),
        ("0.6.1", "0.5.1", "0.6.0"),
        ("0.6.1", "0.5.2", "0.5.2"),
    ],
)
def test_skipped_container_transition_is_limited_to_exact_versions(
    project_version: str, compose_version: str, base_version: str
) -> None:
    with pytest.raises(AlignmentError, match="exact pending"):
        check_alignment(
            project_version=project_version,
            compose=ComposeIdentity(compose_version, REGISTRY_DIGEST),
            event_name="pull_request",
            transport=_skipped_container_transport(),
            base=BaseIdentity(base_version, SOURCE_SHA),
            registry_credentials=REGISTRY_CREDENTIALS,
        )


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
            base=(BaseIdentity(base_version, SOURCE_SHA) if base_version else None),
        )


@pytest.mark.unit
def test_future_unchanged_revision_cannot_reuse_pending_exception() -> None:
    with pytest.raises(AlignmentError, match="exact pending"):
        check_alignment(
            project_version="0.5.0",
            compose=ComposeIdentity("0.4.0", REGISTRY_DIGEST),
            event_name="pull_request",
            transport=_public_transport(),
            base=BaseIdentity("0.5.0", SOURCE_SHA),
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
            base=BaseIdentity("0.4.0", SOURCE_SHA),
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
def test_frontend_compose_parser_requires_one_exact_shared_public_default() -> None:
    image = "ghcr.io/guillaume-lombardo/md-converter-web:0.6.1@sha256:" + "d" * 64
    overlay = (
        "services:\n"
        f'  frontend:\n    image: "${{MARKWEAVE_CUTOVER_FRONTEND_IMAGE:-{image}}}"\n'
        f'  router:\n    image: "${{MARKWEAVE_CUTOVER_FRONTEND_IMAGE:-{image}}}"\n'
    )

    assert parse_frontend_compose_identity(overlay) == ComposeIdentity(
        "0.6.1", f"sha256:{'d' * 64}"
    )
    with pytest.raises(AlignmentError, match="share one trusted immutable"):
        parse_frontend_compose_identity(overlay.replace("md-converter-web", "other", 1))

    unrelated = (
        "services:\n"
        f'  decoy:\n    image: "${{MARKWEAVE_CUTOVER_FRONTEND_IMAGE:-{image}}}"\n'
        + overlay
    )
    assert parse_frontend_compose_identity(unrelated) == ComposeIdentity(
        "0.6.1", f"sha256:{'d' * 64}"
    )
    divergent = overlay.replace(image, image.replace("d" * 64, "e" * 64), 1)
    with pytest.raises(AlignmentError, match="share one trusted immutable"):
        parse_frontend_compose_identity(divergent)


@pytest.mark.parametrize("invalid_image", ["[]", "{}"])
def test_frontend_compose_parser_rejects_non_string_named_images(
    invalid_image: str,
) -> None:
    overlay = (
        "services:\n"
        f"  frontend:\n    image: {invalid_image}\n"
        f"  router:\n    image: {invalid_image}\n"
    )

    with pytest.raises(AlignmentError, match="share one trusted immutable"):
        parse_frontend_compose_identity(overlay)


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
def test_main_binds_skipped_container_exception_to_the_exact_base_sha(
    tmp_path: Path, mocker, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.6.1"\n', encoding="utf-8"
    )
    (tmp_path / "compose.yaml").write_text(
        f"services:\n  markweave:\n    image: {public_alignment.IMAGE_REPOSITORY}:0.5.2@{REGISTRY_DIGEST}\n",
        encoding="utf-8",
    )
    mocker.patch(
        "scripts.release.public_alignment._git_output",
        side_effect=[
            b'[project]\nversion = "0.6.0"\n',
            b'[project]\nversion = "0.6.1"\n',
        ],
    )
    mocker.patch(
        "scripts.release.public_alignment.UrlLibTransport",
        return_value=_skipped_container_transport(),
    )
    monkeypatch.setenv("GHCR_USERNAME", REGISTRY_CREDENTIALS.username)
    monkeypatch.setenv("GHCR_TOKEN", REGISTRY_CREDENTIALS.token)

    result = public_alignment.main(
        [
            "--repository",
            str(tmp_path),
            "--event-name",
            "pull_request",
            "--base",
            SOURCE_SHA,
            "--head",
            "e" * 40,
        ]
    )

    assert result == 0
    assert "alignment: pending-skipped-container" in capsys.readouterr().out


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
