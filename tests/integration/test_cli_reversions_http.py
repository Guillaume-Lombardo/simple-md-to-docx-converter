"""Reverse-job CLI coverage against a real Markweave HTTP application."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import sleep
from typing import Any
from uuid import UUID

import pytest
import uvicorn

from markweave.app import create_app
from markweave.auth.models import normalize_username
from markweave.cli.http import HttpTransport
from markweave.cli.main import main
from markweave.cli.profiles import ProfileStore
from markweave.cli.types import ConnectionProfile
from markweave.config import Settings
from markweave.malware import TrustingUploadScanner
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


@pytest.fixture
def running_reversion_service(tmp_path: Path):
    """Run the production standalone assembly over real loopback HTTP."""

    settings = Settings(
        **template_settings(job_active_limit_per_user=2, job_global_queue_capacity=3),
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
        reversion_upload_max_bytes=1_000_000,
        reversion_request_max_bytes=1_100_000,
        reversion_retry_after_seconds=2,
        reversion_result_retention_seconds=3_600,
        reversion_active_limit_per_user=2,
    )
    application = create_app(settings, scanner=TrustingUploadScanner())
    config = uvicorn.Config(application, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    with config.bind_socket() as listener:
        port = listener.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(500):
                if server.started:
                    break
                sleep(0.01)
            assert server.started
            yield f"http://127.0.0.1:{port}", application
        finally:
            server.should_exit = True
            thread.join(timeout=5)


class _DelayedReversionHandler(BaseHTTPRequestHandler):
    """Hold a real loopback poll request until the client deadline expires."""

    started: Event
    release: Event

    def do_GET(self) -> None:
        self.started.set()
        self.release.wait(timeout=1)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
        except BrokenPipeError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def delayed_reversion_service():
    """Expose one genuine HTTP read that exceeds the CLI's supplied timeout."""
    started = Event()
    release = Event()
    _DelayedReversionHandler.started = started
    _DelayedReversionHandler.release = release
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DelayedReversionHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", started
    finally:
        release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _save_login(
    service_url: str, profile_name: str, username: str, password: str
) -> None:
    response = HttpTransport(service_url, verify_tls=False, timeout=2).login(
        username, password
    )
    assert response.status == 200
    assert response.session is not None and response.payload is not None
    csrf = response.payload.get("csrf_token")
    assert isinstance(csrf, str)
    ProfileStore().save(
        ConnectionProfile(profile_name, service_url, response.session, csrf)
    )


def _last_json(output: str) -> dict[str, Any]:
    value = json.loads(output.splitlines()[-1])
    assert isinstance(value, dict)
    return value


def test_cli_reverse_wait_maps_real_deadline_transport_timeout(
    delayed_reversion_service,
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A final poll transport timeout reports the wait deadline, not a network fault."""
    service_url, started = delayed_reversion_service
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ProfileStore().save(
        ConnectionProfile("default", service_url, "session=value", "csrf")
    )

    assert (
        main(
            (
                "--json",
                "--timeout",
                "0.1",
                "jobs",
                "reverse",
                "wait",
                "11111111-1111-4111-8111-111111111111",
            )
        )
        == 1
    )

    assert started.wait(timeout=1)
    assert _last_json(capsys.readouterr().err)["error"]["code"] == "wait_timeout"


def test_cli_reverse_lifecycle_crosses_real_http_session_and_storage(
    running_reversion_service,
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Installed commands preserve auth, CSRF, idempotency, and owner isolation."""

    service_url, application = running_reversion_service
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _save_login(service_url, "default", "admin", "admin-password")

    assert main(("--json", "jobs", "reverse", "capabilities")) == 0
    capabilities = _last_json(capsys.readouterr().out)["reversion_capabilities"]
    assert capabilities["maximum_upload_bytes"] == 1_000_000
    assert capabilities["execution"] == {
        "local": True,
        "ocr": False,
        "hosted_fallback": False,
    }

    source = tmp_path / "private-quarterly-report.rtf"
    source.write_bytes(b"{\\rtf1 Reverse CLI}")
    arguments = (
        "--json",
        "jobs",
        "reverse",
        "submit",
        str(source),
        "--idempotency-key",
        "reverse-cli-stable",
    )
    assert main(arguments) == 0
    first = _last_json(capsys.readouterr().out)
    job_id = str(UUID(first["id"]))
    assert first["source_stem"] == "private-quarterly-report"
    assert first["source_family"] == "rtf"
    assert first["poll_after_seconds"] == 2

    observer = application.state.components.queue_observer
    assert observer is not None
    snapshot = observer.observe_queue(datetime.now(UTC))
    rendered_metrics = application.state.components.metrics.render(snapshot)
    assert "md_converter_reversion_queue_depth 1" in rendered_metrics
    assert "md_converter_shared_capacity_used 1" in rendered_metrics

    assert main(arguments) == 0
    assert _last_json(capsys.readouterr().out)["id"] == job_id
    assert main(("--json", "jobs", "reverse", "list")) == 0
    listing = _last_json(capsys.readouterr().out)
    assert listing["total"] == 1 and listing["items"][0]["id"] == job_id
    assert main(("--json", "jobs", "reverse", "show", job_id)) == 0
    assert _last_json(capsys.readouterr().out)["state"] == "queued"

    authentication = application.state.components.authentication
    admin = authentication.users.get_by_normalized_username(normalize_username("Admin"))
    assert admin is not None
    authentication.create_user(admin, "alice", "alice-password")
    _save_login(service_url, "alice", "alice", "alice-password")
    assert main(("jobs", "reverse", "show", job_id, "--profile", "alice")) == 1
    assert "not found" in capsys.readouterr().err.lower()

    assert main(("--json", "jobs", "reverse", "cancel", job_id)) == 0
    assert _last_json(capsys.readouterr().out)["state"] == "cancelled"
    destination = tmp_path / "must-not-exist.md"
    assert main(("jobs", "reverse", "download", job_id, str(destination))) == 1
    assert "conflicts with current state" in capsys.readouterr().err.lower()
    assert not destination.exists()
