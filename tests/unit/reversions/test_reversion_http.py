"""HTTP adapter coverage for reverse-conversion lifecycle routes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.http.middleware import BoundedRequestBody
from markweave.jobs.service import JobService
from markweave.malware import UploadScanner
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobRepositoryError,
    ReversionJobStorageError,
    ReversionJobUserQuotaExceededError,
    ReversionQueueCapacityExceededError,
)
from markweave.reversion_jobs.models import (
    ANYDOC_COMPONENT,
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    ReversionTraceMetadata,
)
from markweave.reversion_jobs.service import ReversionService
from markweave.reversions.formats import FormatAdmission, FormatFamily
from markweave.reversions.models import ReverseOutputMode
from tests.reversion_job_repository_contracts import NOW
from tests.settings import template_settings

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "admin-password",
        "storage_profile": "standalone",
        "standalone_data_directory": "/data",
        "conversion_upload_max_bytes": 1_000,
        "conversion_request_max_bytes": 2_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3_600,
        "reversion_upload_max_bytes": 1_000,
        "reversion_request_max_bytes": 2_000,
        "reversion_retry_after_seconds": 2,
        "reversion_result_retention_seconds": 3_600,
        "reversion_active_limit_per_user": 1,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _job(owner: User, *, stem: str = "report") -> ReversionJob:
    return ReversionJob(
        id=uuid4(),
        owner_id=owner.id,
        source_object_id=uuid4(),
        source_stem=stem,
        admission=FormatAdmission(FormatFamily.RTF, ".rtf", "rtf", "rtf"),
        source_sha256=sha256(b"{\\rtf1 report}").hexdigest(),
        source_size=len(b"{\\rtf1 report}"),
        component_versions=(ANYDOC_COMPONENT,),
        request_digest="a" * 64,
        idempotency_digest=None,
        correlation_id="12345678-1234-4234-8234-123456789abc",
        state=ReversionJobState.QUEUED,
        step=ReversionJobStep.QUEUED,
        created_at=NOW,
        updated_at=NOW,
        source_ready=True,
    )


def _client(
    mocker: MockerFixture,
    *,
    settings: Settings | None = None,
    scanner: UploadScanner | None = None,
    reversions: ReversionService | None = None,
) -> tuple[TestClient, User]:
    actor = User(uuid4(), "Alice", "alice", "hash", Role.USER)
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.authenticate.return_value = actor
    app = create_app(
        settings or _settings(),
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(spec=JobService),
            scanner=scanner or mocker.Mock(spec=UploadScanner),
            reversions=reversions,
        ),
    )
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set("md_converter_session", "session")
    return client, actor


def test_submission_scans_before_detection_and_durable_reservation(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    scanner = mocker.Mock(spec=UploadScanner)
    scanner.scan.side_effect = lambda _content: events.append("scan")
    reversions = mocker.Mock(spec=ReversionService)
    client, actor = _client(mocker, scanner=scanner, reversions=reversions)
    expected = _job(actor)

    def submit(request, idempotency_key):
        assert events == ["scan"]
        events.append("submit")
        assert request.owner_id == actor.id
        assert request.admission.detected_format == "rtf"
        assert idempotency_key == "stable-key"
        return expected, False

    reversions.submit.side_effect = submit

    with client:
        response = client.post(
            "/api/v1/reversions",
            headers={"X-CSRF-Token": "csrf", "Idempotency-Key": "stable-key"},
            files={"source": ("report.rtf", b"{\\rtf1 report}", "application/rtf")},
        )

    assert response.status_code == 202
    assert events == ["scan", "submit"]
    assert response.headers["Location"] == f"/api/v1/reversions/{expected.id}"
    assert response.headers["Retry-After"] == "2"


@pytest.mark.parametrize(
    ("mode", "stem", "media_type", "disposition"),
    [
        (
            ReverseOutputMode.MARKDOWN,
            "report",
            "text/markdown; charset=utf-8",
            'attachment; filename="report.md"',
        ),
        (
            ReverseOutputMode.MARKDOWN_WITH_ASSETS,
            "Café",
            "application/zip",
            "attachment; filename*=UTF-8''Caf%C3%A9.zip",
        ),
    ],
)
def test_result_download_preserves_safe_stem_and_private_headers(
    mode: ReverseOutputMode,
    stem: str,
    media_type: str,
    disposition: str,
    mocker: MockerFixture,
) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    client, actor = _client(mocker, reversions=reversions)
    queued = _job(actor, stem=stem)
    content = b"result"
    trace = ReversionTraceMetadata(
        schema_version=1,
        engine_name=ANYDOC_COMPONENT[0],
        engine_version=ANYDOC_COMPONENT[1],
        source_family=FormatFamily.RTF,
        detected_format="rtf",
        result_mode=mode,
        asset_count=1 if mode is ReverseOutputMode.MARKDOWN_WITH_ASSETS else 0,
        asset_bytes=1 if mode is ReverseOutputMode.MARKDOWN_WITH_ASSETS else 0,
        unavailable_asset_count=0,
    )
    succeeded = replace(
        queued,
        state=ReversionJobState.SUCCEEDED,
        step=ReversionJobStep.COMPLETE,
        result_mode=mode,
        result_object_id=uuid4(),
        result_sha256=sha256(content).hexdigest(),
        result_size=len(content),
        trace=trace,
    )
    reversions.download.return_value = succeeded, content

    with client:
        response = client.get(f"/api/v1/reversions/{succeeded.id}/result")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["Content-Type"] == media_type
    assert response.headers["Content-Disposition"] == disposition
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    reversions.download.assert_called_once_with(succeeded.id, actor.id)


def test_submission_fails_closed_before_parsing_when_runtime_is_unconfigured(
    mocker: MockerFixture,
) -> None:
    client, _actor = _client(
        mocker,
        settings=_settings(reversion_request_max_bytes=None),
        reversions=None,
    )

    with client:
        response = client.post(
            "/api/v1/reversions",
            files={"source": ("report.rtf", b"{\\rtf1 report}", "application/rtf")},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REVERSION_SERVICE_UNAVAILABLE"
    assert response.headers["Cache-Control"] == "private, no-store"


def test_unavailable_runtime_rejects_before_reading_any_request_bytes(
    mocker: MockerFixture,
) -> None:
    downstream = mocker.AsyncMock()
    receive = mocker.AsyncMock(side_effect=AssertionError("body must not be read"))
    send = mocker.AsyncMock()
    middleware = BoundedRequestBody(
        downstream,
        conversion_maximum_bytes=2_000,
        reversion_maximum_bytes=None,
        template_maximum_bytes=2_000,
        template_metadata_maximum_bytes=1_000,
    )

    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "path": "/api/v1/reversions"},
            receive,
            send,
        )
    )

    receive.assert_not_called()
    downstream.assert_not_called()
    assert send.await_args_list[0].args[0]["status"] == 503


def test_submission_body_is_bounded_before_multipart_spooling(
    mocker: MockerFixture,
) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    client, _actor = _client(
        mocker,
        settings=_settings(
            reversion_upload_max_bytes=16, reversion_request_max_bytes=32
        ),
        reversions=reversions,
    )

    with client:
        response = client.post(
            "/api/v1/reversions",
            files={"source": ("report.rtf", b"x" * 64, "application/rtf")},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REVERSION_REQUEST_TOO_LARGE"
    reversions.submit.assert_not_called()


def test_owner_lifecycle_routes_delegate_only_the_authenticated_identity(
    mocker: MockerFixture,
) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    client, actor = _client(mocker, reversions=reversions)
    job = _job(actor)
    reversions.list_owner.return_value = mocker.Mock(
        items=(job,), total=1, offset=0, limit=50
    )
    reversions.get.return_value = job
    reversions.cancel.return_value = replace(
        job, state=ReversionJobState.CANCELLED, cancel_requested=True
    )

    with client:
        listing = client.get("/api/v1/reversions")
        fetched = client.get(f"/api/v1/reversions/{job.id}")
        cancelled = client.delete(
            f"/api/v1/reversions/{job.id}", headers={"X-CSRF-Token": "csrf"}
        )

    assert listing.status_code == fetched.status_code == cancelled.status_code == 200
    assert listing.json()["items"] == [fetched.json()]
    assert cancelled.json()["state"] == "cancelled"
    reversions.list_owner.assert_called_once_with(actor.id, offset=0, limit=50)
    reversions.get.assert_called_once_with(job.id, actor.id)
    assert reversions.cancel.call_args.args == (job.id, actor.id)


@pytest.mark.parametrize(
    ("settings", "content", "code"),
    [
        (
            _settings(reversion_retry_after_seconds=None),
            b"{\\rtf1 report}",
            "REVERSION_SERVICE_UNAVAILABLE",
        ),
        (_settings(), b"", "REVERSION_REQUEST_INVALID"),
    ],
)
def test_submission_rejects_incomplete_runtime_or_empty_source(
    settings: Settings, content: bytes, code: str, mocker: MockerFixture
) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    client, _actor = _client(mocker, settings=settings, reversions=reversions)

    with client:
        response = client.post(
            "/api/v1/reversions",
            headers={"X-CSRF-Token": "csrf"},
            files={"source": ("report.rtf", content, "application/rtf")},
        )

    assert response.status_code in {422, 503}
    assert response.json()["error"]["code"] == code
    reversions.submit.assert_not_called()


def test_submission_rejects_source_over_upload_limit_after_bounded_parse(
    mocker: MockerFixture,
) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    client, _actor = _client(
        mocker,
        settings=_settings(
            reversion_upload_max_bytes=8, reversion_request_max_bytes=1_000
        ),
        reversions=reversions,
    )

    with client:
        response = client.post(
            "/api/v1/reversions",
            headers={"X-CSRF-Token": "csrf"},
            files={"source": ("report.rtf", b"{\\rtf1 report}", "application/rtf")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REVERSION_REQUEST_INVALID"
    reversions.submit.assert_not_called()


def test_result_download_rejects_missing_result_mode(mocker: MockerFixture) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    client, actor = _client(mocker, reversions=reversions)
    job = _job(actor)
    reversions.download.return_value = job, b"result"

    with client:
        response = client.get(f"/api/v1/reversions/{job.id}/result")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REVERSION_REQUEST_INVALID"


@pytest.mark.parametrize(
    ("error", "status_code", "code", "retry_after"),
    [
        (
            ReversionJobConflictError(),
            409,
            "REVERSION_CONFLICT",
            None,
        ),
        (
            ReversionJobUserQuotaExceededError(),
            429,
            "REVERSION_USER_QUOTA_EXCEEDED",
            "2",
        ),
        (
            ReversionQueueCapacityExceededError(),
            503,
            "REVERSION_QUEUE_CAPACITY_EXCEEDED",
            "2",
        ),
        (
            ReversionJobRepositoryError(),
            503,
            "REVERSION_STORAGE_UNAVAILABLE",
            None,
        ),
        (
            ReversionJobStorageError(),
            503,
            "REVERSION_STORAGE_UNAVAILABLE",
            None,
        ),
        (ValueError("invalid"), 422, "REVERSION_REQUEST_INVALID", None),
    ],
)
def test_submission_maps_reverse_failures_to_stable_safe_envelopes(
    error: Exception,
    status_code: int,
    code: str,
    retry_after: str | None,
    mocker: MockerFixture,
) -> None:
    reversions = mocker.Mock(spec=ReversionService)
    reversions.submit.side_effect = error
    client, _actor = _client(mocker, reversions=reversions)

    with client:
        response = client.post(
            "/api/v1/reversions",
            headers={"X-CSRF-Token": "csrf"},
            files={"source": ("report.rtf", b"{\\rtf1 report}", "application/rtf")},
        )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    if retry_after is None:
        assert "Retry-After" not in response.headers
    else:
        assert response.headers["Retry-After"] == retry_after
