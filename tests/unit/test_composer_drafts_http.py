"""Composer source admission scans before document validation or persistence."""

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any
from uuid import UUID
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.connections import (
    ConnectionAvailability,
    ConnectionService,
    ConnectionState,
)
from markweave.composer.drafts import ComposerDraft, ComposerProposal, ProposalState
from markweave.composer.revisions import (
    ArtifactReference,
    ComposerRevision,
    RevisionSnapshot,
    SourceReference,
)
from markweave.config import Settings
from markweave.malware import (
    MalwareDetectedError,
    MalwareScannerUnavailableError,
    UploadScanner,
)
from markweave.persistence.composer import (
    SqlComposerRepository,
    SqlConnectionRepository,
)
from markweave.reversion_jobs.service import ReversionJobNotFoundError, ReversionService
from markweave.reversions.manifest import ManifestSource
from markweave.reversions.models import ReverseOutputMode
from markweave.reversions.package import PackageLimits, build_reverse_package
from tests.settings import template_settings


def _client(
    mocker: MockerFixture,
    *,
    allow_model: bool = False,
    reversions: ReversionService | None = None,
) -> tuple[TestClient, Any, Any]:
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=2048,
        composer_http_request_max_bytes=4096,
    )
    user = User(UUID(int=2), "owner", "owner", "hash", Role.USER)
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = user
    authentication.authenticate.return_value = user
    scanner = mocker.Mock(spec=UploadScanner)
    store = mocker.Mock(spec=SqlComposerRepository)
    connections = mocker.Mock(spec=ConnectionService) if allow_model else None
    connection_repository = (
        mocker.Mock(spec=SqlConnectionRepository) if allow_model else None
    )
    if connections is not None:
        connections.availability.return_value = ConnectionAvailability(
            ConnectionState.READY
        )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            scanner=scanner,
            composer_store=store,
            composer_connections=connections,
            composer_connection_repository=connection_repository,
            reversions=reversions,
        ),
    )
    return TestClient(app, base_url="https://testserver"), scanner, store


@pytest.mark.unit
def test_history_routes_validate_and_forward_newest_first_order(
    mocker: MockerFixture,
) -> None:
    client, _scanner, store = _client(mocker)
    draft_id = UUID(int=23)
    endpoints = ("messages", "proposals", "revisions")
    store.list_messages.return_value = ()
    store.list_proposals.return_value = ()
    store.list_revisions.return_value = ()
    with client:
        for endpoint in endpoints:
            path = f"/api/v1/composer/drafts/{draft_id}/{endpoint}"
            descending = client.get(f"{path}?order=desc&limit=2&offset=3")
            default = client.get(path)
            invalid = client.get(f"{path}?order=random")
            assert descending.status_code == default.status_code == 200
            assert invalid.status_code == 422
            assert descending.json()[endpoint] == []
            method = getattr(store, f"list_{endpoint}")
            assert method.call_args_list == [
                mocker.call(UUID(int=2), draft_id, limit=2, offset=3, order="desc"),
                mocker.call(UUID(int=2), draft_id, limit=50, offset=0, order="asc"),
            ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode",
    (
        ReverseOutputMode.MARKDOWN,
        ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
    ),
)
def test_reversion_result_handoff_scans_and_preserves_exact_origin(
    mocker: MockerFixture, mode: ReverseOutputMode
) -> None:
    data = (
        b"# Reversed\n"
        if mode is ReverseOutputMode.MARKDOWN
        else build_reverse_package(
            "# Reversed\n",
            (),
            (None,),
            unavailable_asset_count=1,
            source=ManifestSource("pdf", "pdf"),
            limits=PackageLimits(2048, 2048),
        ).content
    )
    runtime = mocker.Mock(spec=ReversionService)
    owner_id, job_id, result_id, draft_id, source_id = (
        UUID(int=n) for n in range(2, 7)
    )
    job = mocker.Mock(
        id=job_id,
        source_stem="reversed",
        result_mode=mode,
        result_object_id=result_id,
        result_sha256=sha256(data).hexdigest(),
    )
    runtime.download.return_value = job, data
    client, scanner, store = _client(mocker, allow_model=True, reversions=runtime)
    now = datetime.now(UTC)
    media_type = (
        "text/markdown" if mode is ReverseOutputMode.MARKDOWN else "application/zip"
    )
    store.create_draft_with_source.return_value = ComposerDraft(
        draft_id,
        owner_id,
        "reversed",
        SourceReference(
            "reversion_result",
            source_id,
            owner_id,
            sha256(data).hexdigest(),
            "clean",
            media_type,
            job_id,
            result_id,
            sha256(data).hexdigest(),
        ),
        "# Reversed\n",
        1,
        None,
        now,
        now,
    )
    with client:
        response = client.post(
            f"/api/v1/composer/drafts/from-reversion/{job_id}",
            json={},
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 201
    assert response.json()["source_kind"] == "reversion_result"
    assert response.json()["source_media_type"] == media_type
    assert response.json()["content"] == "# Reversed\n"
    runtime.download.assert_called_once_with(job_id, owner_id)
    scanner.scan.assert_called_once_with(data)
    assert store.create_draft_with_source.call_args.args[1] == data
    assert store.create_draft_with_source.call_args.kwargs["source_kind"] == (
        "reversion_result"
    )
    assert store.create_draft_with_source.call_args.kwargs["origin_result_sha256"] == (
        sha256(data).hexdigest()
    )


@pytest.mark.unit
def test_reversion_handoff_denies_other_owner_before_scanning(
    mocker: MockerFixture,
) -> None:
    runtime = mocker.Mock(spec=ReversionService)
    runtime.download.side_effect = ReversionJobNotFoundError
    client, scanner, store = _client(mocker, allow_model=True, reversions=runtime)
    with client:
        response = client.post(
            f"/api/v1/composer/drafts/from-reversion/{UUID(int=7)}",
            json={},
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 404
    scanner.scan.assert_not_called()
    store.create_draft_with_source.assert_not_called()


@pytest.mark.unit
def test_scanned_zip_uses_safe_markdown_entrypoint_and_preserves_source(
    mocker: MockerFixture,
) -> None:
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("document.md", "# Approved package\n")
    data = package.getvalue()
    owner_id = UUID(int=2)
    now = datetime.now(UTC)
    client, scanner, store = _client(mocker, allow_model=True)
    store.create_draft_with_source.return_value = ComposerDraft(
        UUID(int=3),
        owner_id,
        "Package",
        SourceReference(
            "upload", UUID(int=4), owner_id, "a" * 64, "clean", "application/zip"
        ),
        "# Approved package\n",
        1,
        None,
        now,
        now,
    )
    with client:
        response = client.post(
            "/api/v1/composer/drafts",
            files={"source": ("package.zip", data, "application/zip")},
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 201
    assert response.json()["content"] == "# Approved package\n"
    assert response.json()["source_media_type"] == "application/zip"
    scanner.scan.assert_called_once_with(data)
    assert store.create_draft_with_source.call_args.args[1] == data
    assert store.create_draft_with_source.call_args.kwargs["media_type"] == (
        "application/zip"
    )


@pytest.mark.unit
def test_unsafe_zip_is_rejected_after_scan_and_before_storage(
    mocker: MockerFixture,
) -> None:
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("../document.md", "# Escaped\n")
    data = package.getvalue()
    client, scanner, store = _client(mocker, allow_model=True)
    with client:
        response = client.post(
            "/api/v1/composer/drafts",
            files={"source": ("package.zip", data, "application/zip")},
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 422
    scanner.scan.assert_called_once_with(data)
    store.create_draft_with_source.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("media_type", "state", "value"),
    (
        ("application/pdf", ProposalState.ACCEPTED, "# Approved"),
        ("text/markdown", ProposalState.PENDING, None),
        ("text/markdown", ProposalState.REJECTED, None),
        ("text/markdown", ProposalState.EDITED, ""),
        ("text/markdown", ProposalState.ACCEPTED, "x" * 2049),
    ),
)
def test_proposal_publication_rejects_unsupported_or_unapproved_content(
    mocker: MockerFixture,
    media_type: str,
    state: ProposalState,
    value: str | None,
) -> None:
    client, _scanner, store = _client(mocker)
    owner_id, draft_id, source_id, proposal_id = (UUID(int=n) for n in range(2, 6))
    now = datetime.now(UTC)
    store.get_draft.return_value = ComposerDraft(
        draft_id,
        owner_id,
        "Draft",
        SourceReference("upload", source_id, owner_id, "a" * 64, "clean", media_type),
        "",
        3,
        None,
        now,
        now,
    )
    store.get_proposal.return_value = ComposerProposal(
        proposal_id,
        draft_id,
        1,
        state,
        "# Suggested",
        value,
        "manual-suggestion",
        now,
        now,
        owner_id,
    )
    with client:
        response = client.post(
            f"/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}/publish",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": '"3"',
                "Idempotency-Key": "reviewed-1",
            },
        )
    assert response.status_code == 422
    store.publish_revision.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("media_type", "size", "reason"),
    (
        ("application/pdf", 5, "native_semantic_diff_unsupported"),
        ("text/markdown", 131_073, "text_too_large"),
    ),
)
def test_revision_diff_reports_safe_unavailable_status_for_native_or_large_text(
    mocker: MockerFixture, media_type: str, size: int, reason: str
) -> None:
    client, _scanner, store = _client(mocker)
    owner_id, draft_id, source_id, old_id, new_id = (UUID(int=n) for n in range(2, 7))
    now = datetime.now(UTC)
    source = SourceReference(
        "upload", source_id, owner_id, "a" * 64, "clean", "text/markdown"
    )
    snapshot = RevisionSnapshot(source, None, "{}", "{}", None, "human", "capture")
    store.get_revision.side_effect = (
        ComposerRevision(
            old_id,
            draft_id,
            1,
            owner_id,
            snapshot,
            (ArtifactReference(UUID(int=7), "download", "b" * 64, size, media_type),),
            None,
            now,
        ),
        ComposerRevision(
            new_id,
            draft_id,
            2,
            owner_id,
            snapshot,
            (ArtifactReference(UUID(int=8), "download", "c" * 64, size, media_type),),
            None,
            now,
        ),
    )
    store.read_artifact.return_value = b"x" * size
    with client:
        response = client.get(
            f"/api/v1/composer/drafts/{draft_id}/revisions/{new_id}/diff",
            params={"from_revision_id": str(old_id)},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == reason
    assert response.json()["changes"] == []
    assert store.read_artifact.call_count == (2 if media_type == "text/markdown" else 0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("scanner_error", "expected_code"),
    (
        (MalwareDetectedError, "UPLOAD_MALWARE_DETECTED"),
        (MalwareScannerUnavailableError, "UPLOAD_SCANNER_UNAVAILABLE"),
        (None, "COMPOSER_REQUEST_INVALID"),
    ),
)
def test_new_source_is_scanned_before_filename_validation_or_storage(
    mocker: MockerFixture,
    scanner_error: type[Exception] | None,
    expected_code: str,
) -> None:
    client, scanner, store = _client(mocker)
    if scanner_error is not None:
        scanner.scan.side_effect = scanner_error

    with client:
        response = client.post(
            "/api/v1/composer/drafts",
            files={
                "source": ("unsafe.exe", b"untrusted bytes", "application/octet-stream")
            },
            headers={"X-CSRF-Token": "csrf"},
        )

    assert response.json()["error"]["code"] == expected_code
    scanner.scan.assert_called_once_with(b"untrusted bytes")
    store.create_draft_with_source.assert_not_called()


@pytest.mark.unit
def test_retained_drafts_remain_accessible_without_model_service(
    mocker: MockerFixture,
) -> None:
    client, _scanner, store = _client(mocker)
    store.list_drafts.return_value = ()

    with client:
        response = client.get("/api/v1/composer/drafts")
        invalid = client.get("/api/v1/composer/drafts?limit=101")
        capabilities = client.get("/api/v1/composer/capabilities")

    assert response.status_code == 200
    assert response.json() == {"drafts": [], "limit": 50, "offset": 0}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert invalid.status_code == 422
    store.list_drafts.assert_called_once_with(UUID(int=2), limit=50, offset=0)
    assert capabilities.json()["maximum_upload_bytes"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "title", "data"),
    (
        ("../secret.md", None, b"# Source"),
        ("folder\\secret.md", None, b"# Source"),
        ("source.md", "   ", b"# Source"),
        ("source.md", "x" * 257, b"# Source"),
        ("source.md", "bad\x01title", b"# Source"),
        ("source.md", None, b"\xff"),
    ),
)
def test_source_validation_rejects_unsafe_metadata_after_scan(
    mocker: MockerFixture,
    filename: str,
    title: str | None,
    data: bytes,
) -> None:
    client, scanner, store = _client(mocker)

    with client:
        response = client.post(
            "/api/v1/composer/drafts",
            files={"source": (filename, data, "text/markdown")},
            data={} if title is None else {"title": title},
            headers={"X-CSRF-Token": "csrf"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "COMPOSER_REQUEST_INVALID"
    scanner.scan.assert_called_once_with(data)
    store.create_draft_with_source.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("data", (b"", b"x" * 2049))
def test_source_size_is_rejected_before_scanner_or_storage(
    mocker: MockerFixture, data: bytes
) -> None:
    client, scanner, store = _client(mocker)

    with client:
        response = client.post(
            "/api/v1/composer/drafts",
            files={"source": ("source.md", data, "text/markdown")},
            headers={"X-CSRF-Token": "csrf"},
        )

    assert response.status_code == (422 if not data else 413)
    scanner.scan.assert_not_called()
    store.create_draft_with_source.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path", "payload", "headers", "expected_status"),
    (
        (
            "PUT",
            "/api/v1/composer/drafts/{draft}",
            {"title": "T", "content": "C"},
            {},
            428,
        ),
        (
            "POST",
            "/api/v1/composer/drafts/{draft}/messages",
            {"content": "Question"},
            {"If-Match": '"1"'},
            428,
        ),
        (
            "POST",
            "/api/v1/composer/drafts/{draft}/messages",
            {"content": "Question"},
            {"If-Match": '"1"', "Idempotency-Key": "bad key\x01"},
            422,
        ),
        (
            "POST",
            "/api/v1/composer/drafts/{draft}/messages",
            {"content": "Question"},
            {"If-Match": '"1"', "Idempotency-Key": "x" * 129},
            422,
        ),
        (
            "POST",
            "/api/v1/composer/drafts/{draft}/proposals/{proposal}/decision",
            {"state": "rejected"},
            {},
            428,
        ),
    ),
)
def test_draft_mutations_reject_missing_or_unsafe_preconditions_before_storage(  # noqa: PLR0913, PLR0917 - table-driven HTTP cases
    mocker: MockerFixture,
    method: str,
    path: str,
    payload: dict[str, str],
    headers: dict[str, str],
    expected_status: int,
) -> None:
    client, _scanner, store = _client(mocker)
    target = path.format(draft=UUID(int=3), proposal=UUID(int=4))

    with client:
        response = client.request(
            method,
            target,
            json=payload,
            headers={**headers, "X-CSRF-Token": "csrf"},
        )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == (
        "COMPOSER_PRECONDITION_REQUIRED"
        if expected_status == 428
        else "COMPOSER_REQUEST_INVALID"
    )
    store.save_draft.assert_not_called()
    store.add_message.assert_not_called()
    store.decide_proposal.assert_not_called()
