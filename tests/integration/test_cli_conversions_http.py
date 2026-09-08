"""Conversion CLI coverage against a real Markweave HTTP application."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from time import sleep
from typing import Any
from uuid import UUID, uuid4

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
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.sql import managed_database_engine, standalone_database_url
from markweave.storage import ObjectKey, ObjectScope
from tests.settings import template_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def running_conversion_service(tmp_path: Path):
    """Run the production standalone assembly over real loopback HTTP."""
    settings = Settings(
        **template_settings(job_active_limit_per_user=1, job_global_queue_capacity=2),
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
            yield f"http://127.0.0.1:{port}", application, settings
        finally:
            server.should_exit = True
            thread.join(timeout=5)


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
    """Ignore real-server structured access logs captured before CLI output."""
    value = json.loads(output.splitlines()[-1])
    assert isinstance(value, dict)
    return value


def test_cli_conversion_lifecycle_idempotency_capacity_authorization_and_downloads(  # noqa: PLR0915
    running_conversion_service,
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The delivered commands cross only HTTP and retain every lifecycle contract."""
    service_url, application, settings = running_conversion_service
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _save_login(service_url, "default", "admin", "admin-password")
    assert main(("--json", "conversion-options")) == 0
    options = _last_json(capsys.readouterr().out)["conversion_options"]
    assert options == {
        "conversion_upload_max_bytes": 1_000_000,
        "resolved_template": None,
        "template_version_id": None,
        "selection_source": "pandoc_default",
    }
    source = tmp_path / "private-customer-name.md"
    source.write_text("# Durable CLI conversion", encoding="utf-8")

    arguments = (
        "--json",
        "convert",
        str(source),
        "--output",
        "both",
        "--idempotency-key",
        "cli-stable-request",
    )
    assert main(arguments) == 0
    first_output = capsys.readouterr()
    assert first_output.err == ""
    assert source.name not in first_output.out
    first = _last_json(first_output.out)
    job_id = UUID(first["id"])
    assert first["template_mode"] == "pandoc-default"
    assert first["poll_after_seconds"] == 1

    assert main(arguments) == 0
    replay = _last_json(capsys.readouterr().out)
    assert replay["id"] == str(job_id)

    other_source = tmp_path / "other.md"
    other_source.write_text("# Other", encoding="utf-8")
    assert main(("convert", str(other_source))) == 1
    saturated = capsys.readouterr()
    assert "active conversion quota" in saturated.err.lower()
    assert source.name not in saturated.out + saturated.err

    assert main(("--json", "jobs", "list")) == 0
    listing = _last_json(capsys.readouterr().out)
    assert listing["total"] == 1 and listing["items"][0]["id"] == str(job_id)
    assert main(("--json", "jobs", "show", str(job_id))) == 0
    assert _last_json(capsys.readouterr().out)["state"] == "queued"

    authentication = application.state.components.authentication
    admin = authentication.users.get_by_normalized_username(normalize_username("Admin"))
    assert admin is not None
    authentication.create_user(admin, "alice", "alice-password")
    _save_login(service_url, "alice", "alice", "alice-password")
    assert main(("jobs", "show", str(job_id), "--profile", "alice")) == 1
    denied = capsys.readouterr()
    assert "not found" in denied.err.lower()

    assert main(("--json", "jobs", "cancel", str(job_id))) == 0
    cancelled = _last_json(capsys.readouterr().out)
    assert cancelled["state"] == "cancelled"

    result_source = tmp_path / "result.md"
    result_source.write_text("# Result", encoding="utf-8")
    assert main(("--json", "convert", str(result_source), "--output", "pdf")) == 0
    submitted = _last_json(capsys.readouterr().out)
    result_job_id = UUID(submitted["id"])
    now = datetime.now(UTC)
    with managed_database_engine(
        standalone_database_url(settings.standalone_data_directory)
    ) as engine:
        repository = SqlJobRepository(engine)
        claimed = repository.claim(
            "cli-integration-worker", now, now + timedelta(minutes=1)
        )
        assert claimed is not None and claimed.id == result_job_id
        assert claimed.lease_token is not None
        result_id = uuid4()
        manifest_id = uuid4()
        application.state.components.object_store.put(
            ObjectKey(ObjectScope.RESULT, claimed.owner_id, result_id), b"pdf-result"
        )
        application.state.components.object_store.put(
            ObjectKey(ObjectScope.RESULT_MANIFEST, claimed.owner_id, manifest_id),
            b'{"traceability":"ok"}',
        )
        repository.succeed(
            result_job_id,
            "cli-integration-worker",
            claimed.lease_token,
            result_id,
            now,
            now + timedelta(hours=1),
            manifest_id,
        )

    destination = tmp_path / "result.pdf"
    manifest = tmp_path / "manifest.json"
    assert main(("jobs", "download", str(result_job_id), str(destination))) == 0
    assert main(("jobs", "manifest", str(result_job_id), str(manifest))) == 0
    assert destination.read_bytes() == b"pdf-result"
    assert manifest.read_bytes() == b'{"traceability":"ok"}'
    assert main(("jobs", "download", str(result_job_id), str(destination))) == 1
    assert destination.read_bytes() == b"pdf-result"
    assert (
        main(
            (
                "jobs",
                "download",
                str(result_job_id),
                str(destination),
                "--overwrite",
            )
        )
        == 0
    )
    output = capsys.readouterr()
    assert source.name not in output.out + output.err
    sleep(0.05)
