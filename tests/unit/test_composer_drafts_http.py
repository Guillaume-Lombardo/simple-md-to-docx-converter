"""Composer source admission scans before document validation or persistence."""

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.malware import (
    MalwareDetectedError,
    MalwareScannerUnavailableError,
    UploadScanner,
)
from markweave.persistence.composer import SqlComposerRepository
from tests.settings import template_settings


def _client(mocker: MockerFixture) -> tuple[TestClient, Any, Any]:
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
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            scanner=scanner,
            composer_store=store,
        ),
    )
    return TestClient(app, base_url="https://testserver"), scanner, store


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
