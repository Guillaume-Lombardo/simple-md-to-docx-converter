"""Functional ASGI tests for the public authentication and administration API."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from md_converter.app import AppComponents, build_components, create_app
from md_converter.auth.memory import MemoryReadinessProbe
from md_converter.config import Settings


def make_client() -> TestClient:
    """Build an HTTPS ASGI client with intentionally cheap test-only Argon2 settings."""
    password = "admin-" + "password"
    settings = Settings(
        initial_admin_username="Admin",
        initial_admin_password=password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        argon2_parallelism=1,
        session_idle_seconds=60,
        session_absolute_seconds=300,
    )
    return TestClient(create_app(settings), base_url="https://testserver")


def login(
    client: TestClient, username: str = "admin", password: str | None = None
) -> dict[str, Any]:
    resolved_password = password or "admin-password"
    response = client.post(
        "/api/v1/login", json={"username": username, "password": resolved_password}
    )
    assert response.status_code == 200
    return response.json()


def csrf(payload: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(payload["csrf_token"])}


@pytest.mark.functional
def test_health_login_page_docs_and_openapi_contract() -> None:
    with make_client() as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        page = client.get("/login")
        assert page.status_code == 200
        assert 'lang="en"' in page.text
        assert "Sign in" in page.text
        assert client.get("/docs").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert not any("signup" in path or "register" in path for path in paths)


@pytest.mark.functional
def test_readiness_failure_is_cheap_and_stable() -> None:
    password = "admin-" + "password"
    settings = Settings(
        initial_admin_username="admin",
        initial_admin_password=password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
    )
    built = build_components(settings)
    components = AppComponents(
        authentication=built.authentication,
        readiness=MemoryReadinessProbe(ready=False),
    )
    with TestClient(
        create_app(settings, components=components), base_url="https://testserver"
    ) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "NOT_READY"


@pytest.mark.functional
def test_json_login_sets_hardened_cookie_without_exposing_session_token() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/login",
            json={"username": "ADMIN", "password": "admin-password"},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie
        assert "Max-Age=300" in cookie
        body = response.json()
        assert set(body) == {"user", "csrf_token"}
        assert "password" not in response.text.casefold()
        assert client.get("/api/v1/session").json()["username"] == "Admin"


@pytest.mark.functional
def test_invalid_unknown_and_inactive_logins_are_indistinguishable() -> None:
    with make_client() as client:
        wrong = client.post(
            "/api/v1/login", json={"username": "admin", "password": "wrong"}
        )
        unknown = client.post(
            "/api/v1/login", json={"username": "missing", "password": "wrong"}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert (
            wrong.json()
            == unknown.json()
            == {
                "error": {
                    "code": "INVALID_CREDENTIALS",
                    "message": "The username or password is incorrect.",
                }
            }
        )

        admin = login(client)
        created = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "Alice", "password": "alice-password"},
        ).json()
        disabled = client.patch(
            f"/api/v1/admin/users/{created['id']}/active",
            headers=csrf(admin),
            json={"active": False},
        )
        assert disabled.status_code == 200
        inactive = client.post(
            "/api/v1/login",
            json={"username": "alice", "password": "alice-password"},
        )
        assert inactive.status_code == 401
        assert inactive.json() == wrong.json()


@pytest.mark.functional
def test_admin_lifecycle_csrf_authorization_and_revocation() -> None:
    with make_client() as client:
        admin = login(client)
        missing_csrf = client.post(
            "/api/v1/admin/users",
            json={"username": "Alice", "password": "alice-password"},
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["error"]["code"] == "CSRF_REQUIRED"

        alice = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "  Alice  ", "password": "alice-password"},
        )
        assert alice.status_code == 201
        alice_id = alice.json()["id"]
        duplicate = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "\uff21LICE", "password": "other-password"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "USERNAME_TAKEN"
        assert len(client.get("/api/v1/admin/users").json()) == 2

        alice_login = login(client, "alice", "alice-password")
        forbidden = client.post(
            "/api/v1/admin/users",
            headers=csrf(alice_login),
            json={"username": "Bob", "password": "bob-password"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"

        client.cookies.clear()
        admin = login(client)
        disabled = client.patch(
            f"/api/v1/admin/users/{alice_id}/active",
            headers=csrf(admin),
            json={"active": False},
        )
        assert disabled.status_code == 200
        client.cookies.clear()
        client.cookies.set("md_converter_session", "token-from-revoked-session")
        assert client.get("/api/v1/session").status_code == 401

        client.cookies.clear()
        admin = login(client)
        assert (
            client.patch(
                f"/api/v1/admin/users/{alice_id}/active",
                headers=csrf(admin),
                json={"active": True},
            ).status_code
            == 200
        )
        old_alice = login(client, "alice", "alice-password")
        client.cookies.clear()
        admin = login(client)
        reset = client.post(
            f"/api/v1/admin/users/{alice_id}/password",
            headers=csrf(admin),
            json={"password": "new-alice-password"},
        )
        assert reset.status_code == 204
        client.cookies.clear()
        assert (
            client.post(
                "/api/v1/login",
                json={"username": "alice", "password": "alice-password"},
            ).status_code
            == 401
        )
        assert login(client, "alice", "new-alice-password")["user"]["id"] == alice_id
        assert old_alice["csrf_token"] != admin["csrf_token"]


@pytest.mark.functional
def test_csrf_replay_session_rotation_and_logout() -> None:
    with make_client() as first, make_client() as unrelated:
        login(first)
        unrelated_login = login(unrelated)
        replay = first.post(
            "/api/v1/admin/users",
            headers=csrf(unrelated_login),
            json={"username": "Alice", "password": "alice-password"},
        )
        assert replay.status_code == 403

        old_cookie = first.cookies.get("md_converter_session")
        rotated = login(first)
        assert first.cookies.get("md_converter_session") != old_cookie
        logout = first.post("/api/v1/logout", headers=csrf(rotated))
        assert logout.status_code == 204
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert first.get("/api/v1/session").status_code == 401


@pytest.mark.functional
def test_browser_login_has_stable_failure_and_redirect_success() -> None:
    with make_client() as client:
        failed = client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert failed.status_code == 401
        assert "The username or password is incorrect." in failed.text
        success = client.post(
            "/login",
            data={"username": "admin", "password": "admin-password"},
            follow_redirects=False,
        )
        assert success.status_code == 303
        assert success.headers["location"] == "/docs"
