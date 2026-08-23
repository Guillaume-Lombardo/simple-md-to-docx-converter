"""Unit tests for FastAPI wiring with every application port substituted."""

from __future__ import annotations

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


def isolated_client(
    mocker: MockerFixture, *, ready: bool = True
) -> tuple[TestClient, Any, User, User]:
    """Assemble only the HTTP adapter while replacing all application ports."""
    password = "admin-" + "password"
    settings = Settings(
        initial_admin_username="admin",
        initial_admin_password=password,
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
        ),
    )
    return TestClient(app, base_url="https://testserver"), auth, admin, alice


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

        logged_in = client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "admin-password"},
        )
        assert logged_in.status_code == 200
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
        assert client.post("/api/v1/logout", headers=headers).status_code == 204

    auth.validate_csrf.assert_called()
    auth.reset_password.assert_called_once_with(admin, alice.id, "replacement-password")
    auth.logout.assert_called()


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
    }
    for (path, method), statuses in expected.items():
        responses = paths[path][method]["responses"]
        assert set(responses) == statuses
        for status_code in statuses - {"200", "201", "204"}:
            reference = responses[status_code]["content"]["application/json"]["schema"][
                "$ref"
            ]
            assert reference.endswith("/ErrorResponse")
    assert error_responses(422)[422]["model"] is ErrorResponse
