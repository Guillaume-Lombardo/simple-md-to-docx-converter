"""Unit tests for FastAPI wiring with every application port substituted."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from md_converter.app import AppComponents, ErrorResponse, create_app, error_responses
from md_converter.auth.errors import INVALID_CREDENTIALS
from md_converter.auth.memory import MemoryReadinessProbe
from md_converter.auth.models import LoginResult, Role, User
from md_converter.auth.service import AuthenticationService
from md_converter.config import Settings
from md_converter.jobs.errors import (
    JobQueueCapacityExceededError,
    JobUserQuotaExceededError,
)
from md_converter.jobs.models import JobPage, JobState, JobStep
from md_converter.jobs.runner import EmbeddedWorker
from md_converter.jobs.service import JobService
from md_converter.malware import (
    MalwareDetectedError,
    MalwareScannerUnavailableError,
    UploadScanner,
)
from md_converter.observability import QueueObserver
from md_converter.persistence.errors import PersistenceError
from md_converter.templates.models import (
    TemplateIdentity,
    TemplatePage,
    TemplateStatus,
    TemplateVersion,
)
from md_converter.templates.service import TemplateService
from tests.settings import template_settings
from tests.unit.jobs.test_job_models import job


def isolated_client(
    mocker: MockerFixture, *, ready: bool = True, scanner: UploadScanner | None = None
) -> tuple[TestClient, Any, User, User]:
    """Assemble only the HTTP adapter while replacing all application ports."""
    password = "admin-" + "password"
    settings = Settings(
        **template_settings(
            template_max_archive_bytes=1_000,
            template_request_max_bytes=5_000,
        ),
        initial_admin_username="admin",
        initial_admin_password=password,
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    admin = User(uuid4(), "admin", "admin", "admin-hash", Role.ADMIN)
    alice = User(uuid4(), "Alice", "alice", "alice-hash", Role.USER)
    auth = mocker.Mock(spec=AuthenticationService)
    auth.bootstrap_admin.return_value = admin
    auth.login.return_value = LoginResult(admin, "session-token", "csrf-token")
    auth.authenticate.return_value = admin
    auth.list_users.return_value = [admin, alice]
    auth.create_user.return_value = alice
    auth.set_active.return_value = alice
    app = create_app(
        settings,
        components=AppComponents(
            authentication=auth,
            readiness=MemoryReadinessProbe(ready=ready),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(spec=JobService),
            **({"scanner": scanner} if scanner is not None else {}),
        ),
    )
    return TestClient(app, base_url="https://testserver"), auth, admin, alice


def _lifecycle_settings() -> Settings:
    return Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )


def _lifecycle_components(mocker: MockerFixture, engine: Any) -> AppComponents:
    auth = mocker.Mock(spec=AuthenticationService)
    auth.bootstrap_admin.return_value = User(
        uuid4(), "Admin", "admin", "hash", Role.ADMIN
    )
    return AppComponents(
        authentication=auth,
        readiness=MemoryReadinessProbe(),
        object_store=mocker.Mock(),
        jobs=mocker.Mock(spec=JobService),
        queue_observer=mocker.Mock(spec=QueueObserver),
        owned_engines=(engine,),
    )


@pytest.mark.unit
@pytest.mark.parametrize("request_fails", [False, True])
def test_application_lifespan_closes_owned_engines_after_success_or_failure(
    mocker: MockerFixture, request_fails: bool
) -> None:
    engine = mocker.Mock()
    components = _lifecycle_components(mocker, engine)
    mocker.patch("md_converter.app.build_components", return_value=components)
    app = create_app(_lifecycle_settings())
    if request_fails:

        def fail() -> None:
            raise RuntimeError("request failed")

        app.add_api_route("/failure", fail)

    with TestClient(app, base_url="https://testserver") as client:
        if request_fails:
            with pytest.raises(RuntimeError, match="request failed"):
                client.get("/failure")
        else:
            assert client.get("/health/live").status_code == 200

    engine.dispose.assert_called_once_with()


@pytest.mark.unit
def test_application_lifespan_does_not_close_injected_components(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    components = _lifecycle_components(mocker, engine)

    with TestClient(
        create_app(_lifecycle_settings(), components=components),
        base_url="https://testserver",
    ) as client:
        assert client.get("/health/live").status_code == 200

    engine.dispose.assert_not_called()


@pytest.mark.unit
def test_embedded_worker_lifespan_is_owned_and_failure_blocks_readiness(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    components = _lifecycle_components(mocker, engine)
    worker = mocker.Mock(spec=EmbeddedWorker)
    worker.failure = None
    app = create_app(
        _lifecycle_settings(),
        components=components,
        embedded_worker=worker,
        embedded_worker_stop_timeout_seconds=7,
        manage_components=True,
    )

    with TestClient(app, base_url="https://testserver") as client:
        worker.start.assert_called_once_with()
        assert client.get("/health/ready").status_code == 200
        worker.failure = RuntimeError("worker stopped")
        assert client.get("/health/ready").status_code == 503

    worker.stop.assert_called_once_with(timeout_seconds=7)
    engine.dispose.assert_called_once_with()


@pytest.mark.unit
def test_embedded_worker_start_failure_closes_owned_components(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    components = _lifecycle_components(mocker, engine)
    worker = mocker.Mock(spec=EmbeddedWorker)
    worker.start.side_effect = RuntimeError("worker startup failed")
    app = create_app(
        _lifecycle_settings(),
        components=components,
        embedded_worker=worker,
        manage_components=True,
    )

    with (
        pytest.raises(RuntimeError, match="worker startup failed"),
        TestClient(app, base_url="https://testserver"),
    ):
        pass

    worker.stop.assert_not_called()
    engine.dispose.assert_called_once_with()


@pytest.mark.unit
def test_application_factory_closes_owned_engines_on_startup_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    components = _lifecycle_components(mocker, engine)
    mocker.patch.object(
        components.authentication,
        "bootstrap_admin",
        side_effect=RuntimeError("startup failed"),
    )
    mocker.patch("md_converter.app.build_components", return_value=components)

    with pytest.raises(RuntimeError, match="startup failed"):
        create_app(_lifecycle_settings())

    engine.dispose.assert_called_once_with()


@pytest.mark.unit
def test_application_factory_does_not_close_injected_engines_on_startup_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    components = _lifecycle_components(mocker, engine)
    mocker.patch.object(
        components.authentication,
        "bootstrap_admin",
        side_effect=RuntimeError("startup failed"),
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        create_app(_lifecycle_settings(), components=components)

    engine.dispose.assert_not_called()


@pytest.mark.unit
def test_http_adapter_happy_paths_delegate_without_exposing_hashes(
    mocker: MockerFixture,
) -> None:
    client, auth, admin, alice = isolated_client(mocker)
    with client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert "Sign in" in client.get("/login").text

        browser = client.post(
            "/login",
            data={"username": "admin", "password": "admin-password"},
            follow_redirects=False,
        )
        assert browser.status_code == 303
        assert browser.headers["location"] == "/convert"
        browser_cookies = browser.headers.get_list("set-cookie")
        assert "__Host-md_converter_csrf=csrf-token" in browser_cookies[-1]
        assert "Secure" in browser_cookies[-1]
        assert "SameSite=lax" in browser_cookies[-1]
        assert "HttpOnly" not in browser_cookies[-1]
        assert any("HttpOnly" in cookie for cookie in browser_cookies)

        logged_in = client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "admin-password"},
        )
        assert logged_in.status_code == 200
        assert any(
            "__Host-md_converter_csrf=csrf-token" in cookie
            for cookie in logged_in.headers.get_list("set-cookie")
        )
        assert "hash" not in logged_in.text
        headers = {"X-CSRF-Token": "csrf-token"}
        assert client.get("/api/v1/session").json()["id"] == str(admin.id)
        assert len(client.get("/api/v1/admin/users").json()) == 2
        assert client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={"username": "Alice", "password": "alice-password"},
        ).json()["id"] == str(alice.id)
        assert (
            client.patch(
                f"/api/v1/admin/users/{alice.id}/active",
                headers=headers,
                json={"active": False},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/admin/users/{alice.id}/password",
                headers=headers,
                json={"password": "replacement-password"},
            ).status_code
            == 204
        )
        logout = client.post("/api/v1/logout", headers=headers)
        assert logout.status_code == 204
        assert any(
            "__Host-md_converter_csrf=" in cookie and "Max-Age=0" in cookie
            for cookie in logout.headers.get_list("set-cookie")
        )

    auth.validate_csrf.assert_called()
    auth.reset_password.assert_called_once_with(admin, alice.id, "replacement-password")
    auth.logout.assert_called()


@pytest.mark.unit
def test_authenticated_conversion_page_and_assets_are_hardened(
    mocker: MockerFixture,
) -> None:
    client, auth, admin, _alice = isolated_client(mocker)
    templates = mocker.Mock(spec=TemplateService)
    template = TemplateIdentity(
        uuid4(),
        admin.id,
        "Default",
        "Shared",
        TemplateStatus.ACTIVE,
        current_version_id=uuid4(),
    )
    templates.resolve.return_value = template
    templates.selection_label.return_value = "System fallback template"
    object.__setattr__(client.app.state.components, "templates", templates)
    client.app.state.components.jobs.list_owner.return_value = JobPage((), 0, 0, 10)

    with client:
        root = client.get("/", follow_redirects=False)
        page = client.get("/convert")
        script = client.get("/static/conversion.js")
        stylesheet = client.get("/static/conversion.css")
    assert page.status_code == 200
    assert root.status_code == 303 and root.headers["location"] == "/convert"
    assert "System fallback template" in page.text
    assert str(template.current_version_id) in page.text
    assert "default-src 'none'" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-store"
    assert script.headers["X-Content-Type-Options"] == "nosniff"
    assert script.headers["Content-Type"].startswith("text/javascript")
    assert stylesheet.headers["Content-Type"].startswith("text/css")
    templates.resolve.assert_called_once_with(admin)
    auth.authenticate.assert_called()


@pytest.mark.unit
def test_authenticated_administration_page_and_assets_are_hardened(
    mocker: MockerFixture,
) -> None:
    client, auth, admin, _alice = isolated_client(mocker)
    templates = mocker.Mock(spec=TemplateService)
    template = TemplateIdentity(
        uuid4(),
        admin.id,
        "Preferred",
        "Private description",
        TemplateStatus.ACTIVE,
        current_version_id=uuid4(),
    )
    templates.resolve.return_value = template
    templates.selection_label.return_value = "Preferred template"
    object.__setattr__(client.app.state.components, "templates", templates)

    with client:
        page = client.get("/templates")
        script = client.get("/static/administration.js")
        stylesheet = client.get("/static/administration.css")

    assert page.status_code == 200
    assert str(template.id) in page.text
    assert 'data-user-role="admin"' in page.text
    assert page.headers["Cache-Control"] == "no-store"
    assert script.headers["X-Content-Type-Options"] == "nosniff"
    assert script.headers["Content-Type"].startswith("text/javascript")
    assert stylesheet.headers["Content-Type"].startswith("text/css")
    templates.resolve.assert_called_once_with(admin)
    templates.selection_label.assert_called_once_with(admin, template)

    auth.authenticate.side_effect = INVALID_CREDENTIALS.new()
    with client:
        anonymous = client.get("/templates", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"


@pytest.mark.unit
def test_conversion_page_redirects_when_session_is_absent(
    mocker: MockerFixture,
) -> None:
    client, auth, _admin, _alice = isolated_client(mocker)
    auth.authenticate.side_effect = INVALID_CREDENTIALS.new()
    with client:
        response = client.get("/convert", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.unit
def test_http_adapter_handles_browser_authentication_failure(
    mocker: MockerFixture,
) -> None:
    client, auth, _, _ = isolated_client(mocker)
    auth.login.side_effect = INVALID_CREDENTIALS.new()
    with client:
        response = client.post(
            "/login", data={"username": "admin", "password": "wrong"}
        )
    assert response.status_code == 401
    assert "The username or password is incorrect." in response.text


@pytest.mark.unit
def test_login_origin_policy_rejects_hostile_and_allows_same_or_absent_origin(
    mocker: MockerFixture,
) -> None:
    client, auth, _, _ = isolated_client(mocker)
    payload = {"username": "attacker-account", "password": "attacker-password"}
    with client:
        hostile = client.post(
            "/api/v1/login",
            headers={"Origin": "https://attacker.example"},
            json=payload,
        )
        assert hostile.status_code == 403
        assert hostile.json()["error"]["code"] == "LOGIN_ORIGIN_INVALID"
        auth.login.assert_not_called()

        assert (
            client.post(
                "/api/v1/login",
                headers={"Origin": "https://TESTSERVER/"},
                json=payload,
            ).status_code
            == 200
        )
        assert client.post("/api/v1/login", json=payload).status_code == 200


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        ("post", "/api/v1/login", {"json": {"username": "admin"}}),
        (
            "post",
            "/api/v1/login",
            {
                "content": '{"username":"admin","password":"plaintext-secret"',
                "headers": {"Content-Type": "application/json"},
            },
        ),
        (
            "post",
            "/api/v1/admin/users",
            {"json": {"username": "Alice"}},
        ),
        (
            "patch",
            "/api/v1/admin/users/not-a-uuid/active",
            {"json": {"active": False}},
        ),
    ],
)
def test_validation_errors_are_stable_and_never_echo_input(
    mocker: MockerFixture,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
) -> None:
    client, _, _, _ = isolated_client(mocker)
    with client:
        response = client.request(method, path, **request_kwargs)
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "REQUEST_INVALID", "message": "The request is invalid."}
    }
    assert "plaintext-secret" not in response.text
    assert "input" not in response.text.casefold()


@pytest.mark.unit
def test_persistence_errors_are_stable_and_never_echo_sql_or_parameters(
    mocker: MockerFixture,
) -> None:
    client, auth, _, _ = isolated_client(mocker)
    auth.login.side_effect = PersistenceError()
    with client:
        response = client.post(
            "/api/v1/login",
            json={"username": "private-user", "password": "private-password"},
        )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "PERSISTENCE_UNAVAILABLE",
            "message": "Persistent storage is unavailable.",
        }
    }
    assert "private" not in response.text
    assert "sql" not in response.text.casefold()


@pytest.mark.unit
def test_openapi_declares_stable_error_contracts_and_actual_readiness_503(
    mocker: MockerFixture,
) -> None:
    client, _, _, _ = isolated_client(mocker, ready=False)
    with client:
        readiness = client.get("/health/ready")
        assert readiness.status_code == 503
        schema = client.get("/openapi.json").json()

    assert readiness.json()["error"]["code"] == "NOT_READY"
    paths = schema["paths"]
    for path in paths.values():
        for operation_name, operation in path.items():
            if operation_name not in {
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            }:
                continue
            for response in operation["responses"].values():
                assert response["headers"]["X-Correlation-ID"] == {
                    "description": "Server-generated request correlation identifier.",
                    "schema": {"type": "string", "format": "uuid"},
                }
    expected = {
        ("/health/ready", "get"): {"200", "503"},
        ("/api/v1/login", "post"): {"200", "401", "403", "422"},
        ("/api/v1/logout", "post"): {"204", "401", "403", "422"},
        ("/api/v1/session", "get"): {"200", "401"},
        ("/api/v1/admin/users", "get"): {"200", "401", "403"},
        ("/api/v1/admin/users", "post"): {"201", "401", "403", "409", "422"},
        ("/api/v1/admin/users/{user_id}/active", "patch"): {
            "200",
            "401",
            "403",
            "404",
            "422",
        },
        ("/api/v1/admin/users/{user_id}/password", "post"): {
            "204",
            "401",
            "403",
            "404",
            "422",
        },
        ("/api/v1/conversions", "post"): {
            "202",
            "401",
            "403",
            "409",
            "413",
            "422",
            "429",
            "503",
        },
        ("/api/v1/conversions", "get"): {"200", "401", "422", "503"},
        ("/api/v1/conversions/{job_id}", "get"): {
            "200",
            "401",
            "404",
            "422",
            "503",
        },
        ("/api/v1/conversions/{job_id}", "delete"): {
            "200",
            "401",
            "403",
            "404",
            "422",
            "503",
        },
        ("/api/v1/conversions/{job_id}/result", "get"): {
            "200",
            "401",
            "404",
            "409",
            "422",
            "503",
        },
    }
    for (path, method), statuses in expected.items():
        responses = paths[path][method]["responses"]
        assert set(responses) == statuses
        for status_code in statuses - {"200", "201", "202", "204"}:
            reference = responses[status_code]["content"]["application/json"]["schema"][
                "$ref"
            ]
            assert reference.endswith("/ErrorResponse")
    assert error_responses(422)[422]["model"] is ErrorResponse
    result_schema = paths["/api/v1/conversions/{job_id}/result"]["get"]["responses"][
        "200"
    ]["content"]["application/octet-stream"]["schema"]
    assert result_schema == {"type": "string", "format": "binary"}
    docx_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    for path in (
        "/api/v1/templates/{template_id}/content",
        "/api/v1/templates/{template_id}/versions/{version_id}/content",
    ):
        responses = paths[path]["get"]["responses"]
        assert responses["200"]["content"][docx_type]["schema"] == {
            "type": "string",
            "format": "binary",
        }
        assert {"401", "404", "422", "503"} <= responses.keys()


@pytest.mark.unit
def test_conversion_body_is_bounded_before_authentication_and_multipart_parsing(
    mocker: MockerFixture,
) -> None:
    client, auth, _, _ = isolated_client(mocker)
    with client:
        response = client.post(
            "/api/v1/conversions",
            content=b"x" * 1_100_001,
            headers={"Content-Type": "multipart/form-data; boundary=private"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "CONVERSION_REQUEST_TOO_LARGE"
    auth.authenticate.assert_not_called()


@pytest.mark.unit
def test_template_body_is_bounded_before_authentication_and_multipart_parsing(
    mocker: MockerFixture,
) -> None:
    client, auth, _, _ = isolated_client(mocker)
    with client:
        response = client.post(
            "/api/v1/templates",
            content=b"x" * 5_001,
            headers={"Content-Type": "multipart/form-data; boundary=private"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "TEMPLATE_REQUEST_TOO_LARGE"
    auth.authenticate.assert_not_called()

    with client:
        patch_response = client.patch(
            f"/api/v1/templates/{uuid4()}",
            content=b"x" * 4_097,
            headers={"Content-Type": "application/json"},
        )
    assert patch_response.status_code == 413
    assert patch_response.json()["error"]["code"] == "TEMPLATE_REQUEST_TOO_LARGE"


@pytest.mark.unit
def test_conversion_http_adapter_delegates_all_safe_routes(
    mocker: MockerFixture,
) -> None:
    client, _, admin, _ = isolated_client(mocker)
    jobs = client.app.state.components.jobs
    queued = job(owner_id=admin.id)
    succeeded = job(
        owner_id=admin.id,
        state=JobState.SUCCEEDED,
        step=JobStep.COMPLETE,
        progress=100,
        result_object_id=uuid4(),
    )
    jobs.submit.return_value = (queued, False)
    jobs.list_owner.return_value = JobPage((queued,), 1, 0, 50)
    jobs.get_visible.return_value = queued
    jobs.cancel.return_value = queued
    jobs.download.return_value = (succeeded, b"result")
    headers = {"X-CSRF-Token": "csrf-token", "Idempotency-Key": "request"}
    data = {
        "template_id": str(queued.template_id),
        "template_version_id": str(queued.template_version_id),
        "output": queued.output.value,
    }
    with client:
        created = client.post(
            "/api/v1/conversions",
            headers=headers,
            files={"source": ("source.md", b"# source")},
            data=data,
        )
        assert created.status_code == 202
        assert created.headers["Retry-After"] == "1"
        assert client.get("/api/v1/conversions").json()["total"] == 1
        assert client.get(f"/api/v1/conversions/{queued.id}").status_code == 200
        assert (
            client.delete(
                f"/api/v1/conversions/{queued.id}", headers=headers
            ).status_code
            == 200
        )
        result = client.get(f"/api/v1/conversions/{queued.id}/result")
        assert result.content == b"result"
        assert f".{succeeded.output.value}" in result.headers["Content-Disposition"]
        assert result.headers["Cache-Control"] == "private, no-store"
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert (
            client.post(
                "/api/v1/conversions",
                headers=headers,
                files={"source": ("source.md", b"")},
                data=data,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/conversions",
                headers=headers,
                files={"source": ("source.md", b"x" * 1_000_001)},
                data=data,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/conversions",
                headers=headers,
                files={"source": ("source.txt", b"valid")},
                data=data,
            ).status_code
            == 422
        )
        jobs.submit.side_effect = ValueError
        assert (
            client.post(
                "/api/v1/conversions",
                headers=headers,
                files={"source": ("source.md", b"valid")},
                data=data,
            ).status_code
            == 422
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    (
        (
            JobUserQuotaExceededError(),
            429,
            "CONVERSION_USER_QUOTA_EXCEEDED",
        ),
        (
            JobQueueCapacityExceededError(),
            503,
            "CONVERSION_QUEUE_CAPACITY_EXCEEDED",
        ),
    ),
)
def test_conversion_capacity_errors_are_stable_and_retryable(
    mocker: MockerFixture,
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    client, _, admin, _ = isolated_client(mocker)
    queued = job(owner_id=admin.id)
    client.app.state.components.jobs.submit.side_effect = error

    with client:
        response = client.post(
            "/api/v1/conversions",
            headers={"X-CSRF-Token": "csrf-token"},
            files={"source": ("source.md", b"# source")},
            data={
                "template_id": str(queued.template_id),
                "template_version_id": str(queued.template_version_id),
                "output": "docx",
            },
        )

    assert response.status_code == expected_status
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": (
                "The active conversion quota is exhausted."
                if expected_status == 429
                else "The conversion queue is at capacity."
            ),
        }
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    (
        (MalwareDetectedError(), 422, "UPLOAD_MALWARE_DETECTED"),
        (MalwareScannerUnavailableError(), 503, "UPLOAD_SCANNER_UNAVAILABLE"),
    ),
)
def test_upload_scan_fails_before_conversion_persistence_with_stable_errors(
    mocker: MockerFixture,
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    scanner = mocker.Mock(spec=UploadScanner)
    scanner.scan.side_effect = error
    client, _, admin, _ = isolated_client(mocker, scanner=scanner)
    queued = job(owner_id=admin.id)
    with client:
        response = client.post(
            "/api/v1/conversions",
            headers={"X-CSRF-Token": "csrf-token"},
            files={"source": ("private-name.md", b"private content")},
            data={
                "template_id": str(queued.template_id),
                "template_version_id": str(queued.template_version_id),
                "output": "docx",
            },
        )
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    scanner.scan.assert_called_once_with(b"private content")
    client.app.state.components.jobs.submit.assert_not_called()


@pytest.mark.unit
def test_template_upload_scan_fails_before_validation_or_persistence(
    mocker: MockerFixture,
) -> None:
    scanner = mocker.Mock(spec=UploadScanner)
    scanner.scan.side_effect = MalwareScannerUnavailableError
    client, _, _, _ = isolated_client(mocker, scanner=scanner)
    templates = mocker.Mock(spec=TemplateService)
    object.__setattr__(client.app.state.components, "templates", templates)
    with client:
        response = client.post(
            "/api/v1/templates",
            headers={"X-CSRF-Token": "csrf-token"},
            data={
                "name": "Template",
                "description": "Description",
                "expected_fonts": "Calibri",
            },
            files={"content": ("private-name.docx", b"private content")},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPLOAD_SCANNER_UNAVAILABLE"
    scanner.scan.assert_called_once_with(b"private content")
    templates.create_versioned.assert_not_called()


@pytest.mark.unit
def test_template_http_adapter_delegates_contract_and_rejects_bad_etags(
    mocker: MockerFixture,
) -> None:
    client, _auth, admin, _alice = isolated_client(mocker)
    templates = mocker.Mock(spec=TemplateService)
    object.__setattr__(client.app.state.components, "templates", templates)
    template = TemplateIdentity(
        uuid4(),
        admin.id,
        "Name",
        "Description",
        TemplateStatus.ACTIVE,
        1,
        uuid4(),
    )
    version = TemplateVersion(
        template.current_version_id or uuid4(),
        template.id,
        1,
        admin.id,
        "a" * 64,
        10,
        datetime.now(UTC),
        admin.id,
    )
    changed = TemplateIdentity(
        template.id,
        admin.id,
        "Changed",
        "Updated",
        TemplateStatus.ACTIVE,
        2,
        version.id,
    )
    templates.search.return_value = TemplatePage((template,), 1, 0, 20)
    templates.create_versioned.return_value = (template, version)
    templates.get_visible.return_value = template
    templates.update_metadata.return_value = changed
    templates.replace.return_value = (changed, version)
    templates.list_versions.return_value = (version,)
    templates.download.return_value = (template, version, b"docx")
    templates.restore.return_value = (changed, version)
    templates.archive.return_value = changed
    csrf = {"X-CSRF-Token": "csrf-token"}
    etag = f'"template-{template.id}-1"'

    with client:
        assert client.get("/api/v1/templates").json()["total"] == 1
        created = client.post(
            "/api/v1/templates",
            headers=csrf,
            data={
                "name": "Name",
                "description": "Description",
                "expected_fonts": "Calibri",
            },
            files={"content": ("template.docx", b"docx")},
        )
        assert created.status_code == 201
        assert client.get(f"/api/v1/templates/{template.id}").headers["etag"] == etag
        for invalid in (
            None,
            "bad",
            f'"template-{template.id}-x"',
            f'"template-{template.id}-0"',
        ):
            headers = dict(csrf)
            if invalid is not None:
                headers["If-Match"] = invalid
            response = client.patch(
                f"/api/v1/templates/{template.id}",
                headers=headers,
                json={"name": "Changed", "description": "Updated"},
            )
            assert response.status_code in {412, 428}
        patched = client.patch(
            f"/api/v1/templates/{template.id}",
            headers={**csrf, "If-Match": etag},
            json={"name": "Changed", "description": "Updated"},
        )
        assert patched.status_code == 200
        replaced = client.put(
            f"/api/v1/templates/{template.id}/content",
            headers={**csrf, "If-Match": etag},
            files={"content": ("template.docx", b"docx")},
            data={"expected_fonts": "Calibri"},
        )
        assert replaced.status_code == 201
        empty = client.put(
            f"/api/v1/templates/{template.id}/content",
            headers={**csrf, "If-Match": etag},
            files={"content": ("empty.docx", b"")},
            data={"expected_fonts": "Calibri"},
        )
        assert empty.status_code == 422
        assert empty.json()["error"]["code"] == "TEMPLATE_INVALID_PACKAGE"
        exact_limit = client.put(
            f"/api/v1/templates/{template.id}/content",
            headers={**csrf, "If-Match": etag},
            files={"content": ("limit.docx", b"x" * 1_000)},
            data={"expected_fonts": "Calibri"},
        )
        assert exact_limit.status_code == 201
        oversized = client.put(
            f"/api/v1/templates/{template.id}/content",
            headers={**csrf, "If-Match": etag},
            files={"content": ("large.docx", b"x" * 1_001)},
            data={"expected_fonts": "Calibri"},
        )
        assert oversized.status_code == 413
        assert (
            client.get(f"/api/v1/templates/{template.id}/versions").status_code == 200
        )
        assert client.get(f"/api/v1/templates/{template.id}/content").content == b"docx"
        assert (
            client.get(
                f"/api/v1/templates/{template.id}/versions/{version.id}/content"
            ).content
            == b"docx"
        )
        assert (
            client.post(
                f"/api/v1/templates/{template.id}/versions/{version.id}/restore",
                headers={**csrf, "If-Match": etag},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/templates/{template.id}/archive",
                headers={**csrf, "If-Match": etag},
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/v1/templates/{template.id}/preferred", headers=csrf
            ).status_code
            == 204
        )
        assert (
            client.delete("/api/v1/template-preference", headers=csrf).status_code
            == 204
        )
        assert (
            client.put(
                f"/api/v1/templates/{template.id}/system-fallback", headers=csrf
            ).status_code
            == 204
        )
        assert (
            client.delete(
                f"/api/v1/templates/{template.id}",
                headers={**csrf, "If-Match": etag},
            ).status_code
            == 204
        )

    templates.delete.assert_called_once_with(admin, template.id, expected_revision=1)
