"""Installed Composer CLI boundaries over loopback HTTP and private files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from markweave.cli.profiles import ProfileStore
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.integration

CONNECTION_ID = "00000000-0000-4000-8000-000000000101"
DRAFT_ID = "00000000-0000-4000-8000-000000000303"
REVISION_ID = "00000000-0000-4000-8000-000000000505"
STEP_ID = "00000000-0000-4000-8000-000000000606"


def _step_response(*, status: str = "running") -> bytes:
    return json.dumps(
        {
            "id": STEP_ID,
            "draft_id": DRAFT_ID,
            "connection_id": CONNECTION_ID,
            "model_identity": "small-model",
            "base_version": 2,
            "status": status,
            "proposal_id": None,
            "error_code": None,
            "created_at": "2026-09-23T00:00:00Z",
            "updated_at": "2026-09-23T00:00:00Z",
        }
    ).encode()


@contextmanager
def _service(
    *,
    artifact_headers: dict[str, str] | None = None,
    model_error_text: str | None = None,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            if self.path.endswith("/model-steps"):
                if model_error_text is not None:
                    payload = json.dumps(
                        {
                            "error": {
                                "code": "PROVIDER_REJECTED",
                                "message": model_error_text,
                            }
                        }
                    ).encode()
                    self.send_response(422)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                payload = _step_response()
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = json.dumps(
                {"id": CONNECTION_ID, "etag": '"composer-connection-1"'}
            ).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_PUT(self) -> None:
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            payload = json.dumps(
                {"id": CONNECTION_ID, "etag": '"composer-connection-2"'}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.path == "/api/v1/composer/capabilities":
                content = json.dumps(
                    {
                        "maximum_credential_bytes": 4096,
                        "maximum_model_request_bytes": 4096,
                        "maximum_output_tokens": 64,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if self.path == f"/api/v1/composer/connections/{CONNECTION_ID}":
                content = json.dumps(
                    {
                        "id": CONNECTION_ID,
                        "endpoint": "https://llm.example/v1",
                        "selected_model": "small-model",
                        "enabled": True,
                        "authorized": True,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if self.path.endswith(f"/model-steps/{STEP_ID}"):
                content = _step_response()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            content = b"untrusted artifact response"
            self.send_response(200)
            for name, value in (artifact_headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_DELETE(self) -> None:
            content = _step_response(status="cancelled")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _profile(tmp_path: Path, service_url: str) -> dict[str, str]:
    state_home = tmp_path / "state"
    ProfileStore(state_home).save(
        ConnectionProfile("default", service_url, "session=opaque", "csrf-opaque")
    )
    return {**os.environ, "XDG_STATE_HOME": str(state_home)}


def _private_file(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)


def _run(
    env: dict[str, str], *arguments: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).with_name("markweave")
    return subprocess.run(
        [str(executable), *arguments],
        env=env,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=15,
        check=False,
    )


def test_installed_cli_sends_multiline_pem_without_exposing_values(
    tmp_path: Path,
) -> None:
    certificate = (
        "-----BEGIN CERTIFICATE-----\nopaque-cert\n-----END CERTIFICATE-----\n"
    )
    private_key = "-----BEGIN PRIVATE KEY-----\nopaque-key\n-----END PRIVATE KEY-----\n"
    internal_ca = "-----BEGIN CERTIFICATE-----\nopaque-ca\n-----END CERTIFICATE-----\n"
    paths = [tmp_path / name for name in ("client.pem", "key.pem", "ca.pem")]
    for path, value in zip(paths, (certificate, private_key, internal_ca), strict=True):
        _private_file(path, value)

    with _service() as (url, received):
        result = _run(
            _profile(tmp_path, url),
            "--non-interactive",
            "composer",
            "connections",
            "credentials",
            "rotate",
            CONNECTION_ID,
            "--etag",
            '"composer-connection-1"',
            "--client-certificate-file",
            str(paths[0]),
            "--client-private-key-file",
            str(paths[1]),
            "--internal-ca-file",
            str(paths[2]),
        )

    assert result.returncode == 0, result.stderr
    assert received == [
        {
            "client_certificate": certificate,
            "client_private_key": private_key,
            "internal_ca": internal_ca,
        }
    ]
    assert all(
        value not in result.stdout + result.stderr
        for value in (certificate, private_key, internal_ca)
    )


def test_personal_connection_identity_rejected_before_post_and_sent_individual(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "api-key"
    _private_file(key_path, "opaque-key")
    with _service() as (url, received):
        env = _profile(tmp_path, url)
        base = (
            "--non-interactive",
            "composer",
            "connections",
            "create",
            "--name",
            "Personal",
            "--endpoint",
            "https://llm.example/v1",
            "--scope",
            "personal",
            "--with-credentials",
            "--api-key-file",
            str(key_path),
        )
        rejected = _run(env, *base, "--identity-mode", "shared")
        assert rejected.returncode == 1
        assert received == []
        accepted = _run(env, *base, "--identity-mode", "individual")

    assert accepted.returncode == 0, accepted.stderr
    assert received[0]["identity_mode"] == "individual"
    assert received[0]["scope"] == "personal"
    assert received[1] == {"api_key": "opaque-key"}
    assert "opaque-key" not in accepted.stdout + accepted.stderr


def test_installed_cli_starts_reads_and_cancels_approved_model_step(
    tmp_path: Path,
) -> None:
    content = "Please revise the approved finding.\nKeep its citation unchanged.\n"
    content_file = tmp_path / "approved.txt"
    _private_file(content_file, content)
    with _service() as (url, received):
        env = _profile(tmp_path, url)
        started = _run(
            env,
            "--json",
            "--non-interactive",
            "composer",
            "model-steps",
            "start",
            DRAFT_ID,
            CONNECTION_ID,
            "--content-file",
            str(content_file),
            "--max-output-tokens",
            "32",
            "--etag",
            '"draft-2"',
            "--idempotency-key",
            "approved-1",
            "--force",
        )
        status = _run(
            env, "--json", "composer", "model-steps", "status", DRAFT_ID, STEP_ID
        )
        cancelled = _run(
            env,
            "--json",
            "--non-interactive",
            "composer",
            "model-steps",
            "cancel",
            DRAFT_ID,
            STEP_ID,
            "--force",
        )

    assert started.returncode == 0, started.stderr
    assert status.returncode == 0, status.stderr
    assert cancelled.returncode == 0, cancelled.stderr
    assert received == [
        {
            "connection_id": CONNECTION_ID,
            "approved_endpoint": "https://llm.example/v1",
            "approved_model": "small-model",
            "content": content,
            "max_output_tokens": 32,
        }
    ]
    assert json.loads(started.stdout)["model_step"]["id"] == STEP_ID
    assert json.loads(cancelled.stdout)["model_step"]["status"] == "cancelled"
    assert all(
        content not in run.stdout + run.stderr for run in (started, status, cancelled)
    )


def test_installed_cli_accepts_stdin_with_explicit_force(tmp_path: Path) -> None:
    content = "User-authored stdin text\nwith two lines.\n"
    with _service() as (url, received):
        result = _run(
            _profile(tmp_path, url),
            "--non-interactive",
            "composer",
            "model-steps",
            "start",
            DRAFT_ID,
            CONNECTION_ID,
            "--stdin",
            "--max-output-tokens",
            "16",
            "--etag",
            '"draft-2"',
            "--idempotency-key",
            "stdin-1",
            "--force",
            input_text=content,
        )

    assert result.returncode == 0, result.stderr
    assert received[0]["content"] == content
    assert content not in result.stdout + result.stderr


def test_installed_cli_redacts_server_echo_of_private_model_text(
    tmp_path: Path,
) -> None:
    content = "private text echoed by provider"
    content_file = tmp_path / "approved.txt"
    _private_file(content_file, content)
    with _service(model_error_text=f"Rejected text: {content}") as (url, received):
        result = _run(
            _profile(tmp_path, url),
            "--json",
            "--non-interactive",
            "composer",
            "model-steps",
            "start",
            DRAFT_ID,
            CONNECTION_ID,
            "--content-file",
            str(content_file),
            "--max-output-tokens",
            "16",
            "--etag",
            '"draft-2"',
            "--idempotency-key",
            "echo-1",
            "--force",
        )

    assert result.returncode == 1
    assert received[0]["content"] == content
    assert content not in result.stdout + result.stderr
    assert json.loads(result.stderr)["error"]["code"] == "model_step_start_failed"


@pytest.mark.parametrize(
    "headers",
    (
        {"X-Content-Type-Options": "nosniff", "X-Composer-Revision": CONNECTION_ID},
        {"X-Content-Type-Options": "sniff", "X-Composer-Revision": REVISION_ID},
        {"X-Composer-Revision": REVISION_ID},
    ),
)
def test_malformed_artifact_headers_preserve_existing_force_output(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    output = tmp_path / "existing.docx"
    output.write_bytes(b"approved revision")
    with _service(artifact_headers=headers) as (url, _received):
        result = _run(
            _profile(tmp_path, url),
            "composer",
            "revisions",
            "download",
            DRAFT_ID,
            REVISION_ID,
            "--kind",
            "download",
            "--output",
            str(output),
            "--force",
        )

    assert result.returncode == 1
    assert "invalid artifact response" in result.stderr
    assert output.read_bytes() == b"approved revision"
    assert not list(tmp_path.glob(".markweave-download-*"))


def test_valid_artifact_headers_publish_owner_only_file(tmp_path: Path) -> None:
    output = tmp_path / "revision.docx"
    with _service(
        artifact_headers={
            "X-Content-Type-Options": "nosniff",
            "X-Composer-Revision": REVISION_ID,
        }
    ) as (url, _received):
        result = _run(
            _profile(tmp_path, url),
            "composer",
            "revisions",
            "download",
            DRAFT_ID,
            REVISION_ID,
            "--kind",
            "download",
            "--output",
            str(output),
        )

    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == b"untrusted artifact response"
    assert output.stat().st_mode & 0o777 == 0o600
