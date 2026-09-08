"""Integration test for a real Uvicorn TCP boundary and security adapters."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import httpx
import pytest
import uvicorn

from markweave.app import create_app
from markweave.config import Settings
from tests.settings import template_settings


@contextmanager
def running_server(settings: Settings) -> Iterator[str]:
    """Run Uvicorn on an ephemeral loopback port and always stop its thread."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), log_level="error", lifespan="off")
    )
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("Uvicorn did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Uvicorn did not stop")


def _assert_reversion_hidden(
    client: httpx.Client, headers: dict[str, str], job_id: str
) -> None:
    for method, suffix in (
        (client.get, ""),
        (client.get, "/result"),
        (client.delete, ""),
    ):
        response = method(f"/api/v1/reversions/{job_id}{suffix}", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "REVERSION_NOT_FOUND"


def _create_user(
    client: httpx.Client, headers: dict[str, str], username: str, password: str
) -> None:
    response = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={"username": username, "password": password},
    )
    assert response.status_code == 201


@pytest.mark.integration
def test_real_argon2_http_session_and_logout_cycle(tmp_path: Path) -> None:
    password = "admin-" + "password"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=password,
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    with (
        running_server(settings) as base_url,
        httpx.Client(base_url=base_url) as client,
    ):
        failure = client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert failure.status_code == 401

        login = client.post(
            "/api/v1/login",
            headers={"Origin": base_url},
            json={"username": "admin", "password": password},
        )
        assert login.status_code == 200
        cookie = login.cookies.get("md_converter_session")
        session_headers = {"Cookie": f"md_converter_session={cookie}"}
        assert client.get("/api/v1/session", headers=session_headers).status_code == 200

        logout = client.post(
            "/api/v1/logout",
            headers={
                **session_headers,
                "X-CSRF-Token": login.json()["csrf_token"],
            },
        )
        assert logout.status_code == 204
        assert client.get("/api/v1/session", headers=session_headers).status_code == 401


@pytest.mark.integration
def test_reversion_capabilities_cross_real_session_and_http_boundaries(
    tmp_path: Path,
) -> None:
    password = "admin-" + "password"

    def settings(data_directory: Path, upload_limit: int | None) -> Settings:
        return Settings(
            **template_settings(),
            initial_admin_username="admin",
            initial_admin_password=password,
            storage_profile="standalone",
            standalone_data_directory=data_directory,
            conversion_upload_max_bytes=1_000_000,
            conversion_request_max_bytes=1_100_000,
            reversion_upload_max_bytes=upload_limit,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=3_600,
        )

    with (
        running_server(settings(tmp_path / "configured", 4_194_304)) as base_url,
        httpx.Client(base_url=base_url) as client,
    ):
        anonymous = client.get("/api/v1/reversions/capabilities")
        login = client.post(
            "/api/v1/login",
            headers={"Origin": base_url},
            json={"username": "admin", "password": password},
        )
        cookie = login.cookies.get("md_converter_session")
        session_headers = {"Cookie": f"md_converter_session={cookie}"}
        first = client.get("/api/v1/reversions/capabilities", headers=session_headers)
        second = client.get("/api/v1/reversions/capabilities", headers=session_headers)

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert login.status_code == 200
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["Cache-Control"] == "private, no-store"
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert first.json()["maximum_upload_bytes"] == 4_194_304

    with (
        running_server(settings(tmp_path / "unavailable", None)) as base_url,
        httpx.Client(base_url=base_url) as client,
    ):
        login = client.post(
            "/api/v1/login",
            headers={"Origin": base_url},
            json={"username": "admin", "password": password},
        )
        cookie = login.cookies.get("md_converter_session")
        unavailable = client.get(
            "/api/v1/reversions/capabilities",
            headers={"Cookie": f"md_converter_session={cookie}"},
        )

    assert login.status_code == 200
    assert unavailable.status_code == 503
    assert unavailable.headers["Cache-Control"] == "private, no-store"
    assert unavailable.headers["X-Content-Type-Options"] == "nosniff"
    assert unavailable.json() == {
        "error": {
            "code": "REVERSION_CAPABILITIES_UNAVAILABLE",
            "message": "Reverse-conversion capabilities are unavailable.",
        }
    }


@pytest.mark.integration
def test_reverse_submission_and_owner_lifecycle_cross_real_session_http_and_storage(
    tmp_path: Path,
) -> None:
    admin_password = "admin-" + "password"
    alice_password = "alice-" + "password"
    bob_password = "bob-" + "password"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=admin_password,
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        insecure_evaluation_mode=True,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
        reversion_upload_max_bytes=1_000_000,
        reversion_request_max_bytes=1_100_000,
        reversion_retry_after_seconds=2,
        reversion_result_retention_seconds=3_600,
        reversion_active_limit_per_user=2,
    )

    with running_server(settings) as base_url:  # noqa: SIM117 - clients use URL
        with httpx.Client(base_url=base_url) as admin:
            login = admin.post(
                "/api/v1/login",
                headers={"Origin": base_url},
                json={"username": "admin", "password": admin_password},
            )
            assert login.status_code == 200
            admin_session = login.cookies.get("md_converter_session")
            admin_headers = {
                "Cookie": f"md_converter_session={admin_session}",
                "X-CSRF-Token": login.json()["csrf_token"],
            }
            _create_user(admin, admin_headers, "alice", alice_password)
            _create_user(admin, admin_headers, "bob", bob_password)

            with httpx.Client(base_url=base_url) as alice:
                alice_login = alice.post(
                    "/api/v1/login",
                    headers={"Origin": base_url},
                    json={"username": "alice", "password": alice_password},
                )
                assert alice_login.status_code == 200
                alice_session = alice_login.cookies.get("md_converter_session")
                mutation_headers = {
                    "Cookie": f"md_converter_session={alice_session}",
                    "X-CSRF-Token": alice_login.json()["csrf_token"],
                    "Idempotency-Key": "reverse-http-cycle",
                }
                submitted = alice.post(
                    "/api/v1/reversions",
                    headers=mutation_headers,
                    files={
                        "source": (
                            "quarterly-report.rtf",
                            b"{\\rtf1 Quarterly report}",
                            "application/rtf",
                        )
                    },
                )
                replayed = alice.post(
                    "/api/v1/reversions",
                    headers=mutation_headers,
                    files={
                        "source": (
                            "quarterly-report.rtf",
                            b"{\\rtf1 Quarterly report}",
                            "application/rtf",
                        )
                    },
                )

                assert submitted.status_code == replayed.status_code == 202
                assert submitted.json() == replayed.json()
                assert submitted.headers["Location"] == replayed.headers["Location"]
                assert submitted.headers["Retry-After"] == "2"
                assert submitted.headers["Cache-Control"] == "private, no-store"
                assert submitted.headers["X-Content-Type-Options"] == "nosniff"
                job_id = submitted.json()["id"]
                assert submitted.json()["source_stem"] == "quarterly-report"
                assert submitted.json()["source_family"] == "rtf"
                assert submitted.json()["detected_format"] == "rtf"
                assert submitted.json()["state"] == "queued"

                owner_headers = {"Cookie": f"md_converter_session={alice_session}"}
                listing = alice.get("/api/v1/reversions", headers=owner_headers)
                status_response = alice.get(
                    f"/api/v1/reversions/{job_id}", headers=owner_headers
                )
                assert listing.status_code == status_response.status_code == 200
                assert listing.json()["total"] == 1
                assert listing.json()["items"] == [status_response.json()]

                _assert_reversion_hidden(admin, admin_headers, job_id)

                with httpx.Client(base_url=base_url) as bob:
                    bob_login = bob.post(
                        "/api/v1/login",
                        headers={"Origin": base_url},
                        json={"username": "bob", "password": bob_password},
                    )
                    assert bob_login.status_code == 200
                    bob_session = bob_login.cookies.get("md_converter_session")
                    bob_headers = {
                        "Cookie": f"md_converter_session={bob_session}",
                        "X-CSRF-Token": bob_login.json()["csrf_token"],
                    }
                    assert (
                        bob.get("/api/v1/reversions", headers=bob_headers).json()[
                            "total"
                        ]
                        == 0
                    )
                    _assert_reversion_hidden(bob, bob_headers, job_id)

                cancelled = alice.delete(
                    f"/api/v1/reversions/{job_id}",
                    headers={
                        "Cookie": f"md_converter_session={alice_session}",
                        "X-CSRF-Token": alice_login.json()["csrf_token"],
                    },
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["state"] == "cancelled"
                unavailable = alice.get(
                    f"/api/v1/reversions/{job_id}/result", headers=owner_headers
                )
                assert unavailable.status_code == 409
                assert unavailable.json()["error"]["code"] == "REVERSION_CONFLICT"


@pytest.mark.integration
def test_public_origin_is_enforced_across_real_http_boundary(tmp_path: Path) -> None:
    password = "admin-" + "password"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=password,
        public_origin="https://converter.example",
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    payload = {"username": "admin", "password": password}
    forwarded_headers = {
        "Forwarded": "host=attacker.example;proto=https",
        "X-Forwarded-Host": "attacker.example",
        "X-Forwarded-Proto": "https",
    }

    with (
        running_server(settings) as base_url,
        httpx.Client(base_url=base_url) as client,
    ):
        accepted = client.post(
            "/api/v1/login",
            headers={"Origin": "https://converter.example", **forwarded_headers},
            json=payload,
        )
        spoofed = client.post(
            "/api/v1/login",
            headers={"Origin": "https://attacker.example", **forwarded_headers},
            json=payload,
        )

    assert accepted.status_code == 200
    assert spoofed.status_code == 403
    assert spoofed.json()["error"]["code"] == "LOGIN_ORIGIN_INVALID"


@pytest.mark.integration
def test_disabled_origin_validation_accepts_tunneled_browser_origins(
    tmp_path: Path,
) -> None:
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        public_origin="https://configured.example",
        insecure_evaluation_mode=True,
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    payload = {"username": "origin-probe", "password": "invalid-origin-probe"}

    with (
        running_server(settings) as base_url,
        httpx.Client(base_url=base_url) as client,
    ):
        responses = [
            client.post("/api/v1/login", headers={"Origin": origin}, json=payload)
            for origin in ("null", "https://attacker.example")
        ]

    assert [response.status_code for response in responses] == [401, 401]
