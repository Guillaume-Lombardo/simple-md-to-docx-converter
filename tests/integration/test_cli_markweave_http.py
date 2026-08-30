"""CLI transport coverage against a running Markweave ASGI application."""

from __future__ import annotations

import socket
from pathlib import Path
from threading import Thread
from time import sleep

import pytest
import uvicorn

from markweave.app import create_app
from markweave.cli.http import HttpTransport
from markweave.cli.types import ConnectionProfile
from markweave.config import Settings
from tests.settings import template_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def running_markweave(tmp_path: Path):
    """Expose the real application over loopback TCP, not an in-process client."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        **template_settings(),
        initial_admin_username="Admin",
        initial_admin_password="admin-password",  # noqa: S106
        argon2_memory_cost=8,
        argon2_time_cost=1,
        argon2_parallelism=1,
        storage_profile="standalone",
        standalone_data_directory=tmp_path / "data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_cli_transport_login_rotation_session_and_logout(
    running_markweave: str,
) -> None:
    """The real Markweave contract rotates, inspects, and revokes CLI sessions."""
    transport = HttpTransport(running_markweave, verify_tls=False, timeout=2)
    first = transport.login("admin", "admin-password")
    assert (
        first.status == 200 and first.session is not None and first.payload is not None
    )
    first_profile = ConnectionProfile(
        "default", running_markweave, first.session, first.payload["csrf_token"]
    )
    rotated = transport.login("admin", "admin-password", previous_profile=first_profile)
    assert (
        rotated.status == 200
        and rotated.session is not None
        and rotated.payload is not None
    )
    rotated_profile = ConnectionProfile(
        "default", running_markweave, rotated.session, rotated.payload["csrf_token"]
    )
    assert transport.session(first_profile).status == 401
    assert transport.session(rotated_profile).status == 200
    assert transport.logout(rotated_profile).status == 204
    assert transport.session(rotated_profile).status == 401
