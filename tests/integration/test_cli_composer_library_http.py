"""Library CLI against the real standalone application over loopback HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from time import sleep
from typing import Any

import pytest
import uvicorn

from markweave.app import create_app
from markweave.cli.main import main
from markweave.config import Settings
from markweave.malware import TrustingUploadScanner
from tests.integration.test_cli_reversions_http import _last_json, _save_login
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_NOTES = b"# Findings\n\nThe northern turbine failed after the storm.\n"


@pytest.fixture
def running_library_service(tmp_path: Path):
    """Run the production standalone assembly with the Composer library."""

    key = tmp_path / "composer-key"
    key.write_text((b"k" * 32).hex(), encoding="ascii")
    key.chmod(0o600)
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
        composer_enabled=True,
        composer_admin_policy_delegated=True,
        composer_allowed_destinations=[],
        composer_allowed_networks=[],
        composer_secret_key_path=key,
        composer_upload_max_bytes=40_000,
        composer_http_request_max_bytes=50_000,
        composer_maximum_request_bytes=4096,
        composer_maximum_response_bytes=4096,
        composer_maximum_models=8,
        composer_maximum_allowed_users=100,
        composer_maximum_model_name_length=128,
        composer_maximum_credential_bytes=8192,
        composer_maximum_output_tokens=128,
        composer_maximum_concurrent_calls=2,
        composer_retry_after_seconds=3,
        composer_timeout_seconds=5.0,
        composer_pending_publication_stale_seconds=30.0,
        composer_draft_retention_seconds=3600,
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def _json(capsys: pytest.CaptureFixture[str], arguments: tuple[str, ...]) -> Any:
    assert main(("--json", *arguments)) == 0, capsys.readouterr().err
    return _last_json(capsys.readouterr().out)


def test_cli_library_lifecycle_crosses_real_http_storage_and_search(
    running_library_service: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _save_login(running_library_service, "default", "admin", "admin-password")
    source = tmp_path / "findings.md"
    source.write_bytes(_NOTES)

    created = _json(
        capsys, ("composer", "library", "upload", str(source), "--title", "Notes")
    )["document"]
    assert created["title"] == "Notes"
    hits = _json(capsys, ("composer", "library", "search", "turbine storm"))
    assert [(hit["title"], hit["chunk"]) for hit in hits["results"]] == [("Notes", 0)]
    citation = f"{created['current_version_id']}:0"
    excerpts = _json(capsys, ("composer", "library", "excerpts", "--cite", citation))
    assert "northern turbine" in excerpts["excerpts"][0]["text"]

    output = tmp_path / "download.md"
    download = (
        "composer",
        "library",
        "download",
        created["id"],
        created["current_version_id"],
        "--output",
        str(output),
    )
    _json(capsys, download)
    assert output.read_bytes() == _NOTES

    replacement = tmp_path / "update.txt"
    replacement.write_bytes(b"The southern array is healthy.\n")
    replace = ("composer", "library", "replace", created["id"], str(replacement))
    replaced = _json(capsys, (*replace, "--etag", created["etag"]))["document"]
    assert replaced["version"] == 2
    versions = _json(capsys, ("composer", "library", "versions", created["id"]))
    assert [item["number"] for item in versions["versions"]] == [1, 2]
    assert main(("--json", *replace, "--etag", created["etag"])) != 0
    error = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert error["error"]["code"] == "composer_library_changed"

    delete = ("composer", "library", "delete", created["id"])
    _json(capsys, (*delete, "--etag", replaced["etag"]))
    listed = _json(capsys, ("composer", "library", "list"))
    assert listed["documents"] == []
