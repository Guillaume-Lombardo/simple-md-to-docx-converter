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
