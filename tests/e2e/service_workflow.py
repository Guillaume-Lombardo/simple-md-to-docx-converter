"""Profile-agnostic service workflow against the deployed final image.

This module is an executable E2E driver, not a Pytest test. Container orchestration
invokes its subcommands before and after disruptive scenarios. Failure evidence is
deliberately bounded and never contains credentials or uploaded document bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import secrets
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.container.api_workflow_smoke import (  # noqa: E402 - executable path bootstrap
    candidate_reference,
    multipart,
)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "expired"})
REQUIRED_METRICS = frozenset(
    {
        "md_converter_queue_depth",
        "md_converter_queue_oldest_age_seconds",
        "md_converter_active_jobs",
    }
)
EXPECTED_FONTS = (
    "Aptos",
    "Aptos Display",
    "Calibri",
    "Cambria",
    "Cambria Math",
    "Consolas",
    "Courier New",
    "Times New Roman",
)


class WorkflowFailure(RuntimeError):
    """A concise failure safe to persist as E2E evidence."""


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes
    set_cookies: tuple[str, ...]


class ServiceClient:
    """Small HTTP(S) client with explicit secure-session cookie handling."""

    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("base URL must not contain a query or fragment")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._prefix = parsed.path.rstrip("/")
        self._cookie = ""
        self.csrf = ""

    def request(  # noqa: PLR0913 - explicit transport options
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        mutate: bool = False,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        request_headers = dict(headers or {})
        if self._cookie:
            request_headers["Cookie"] = self._cookie
        if content_type:
            request_headers["Content-Type"] = content_type
        if mutate:
            request_headers["X-CSRF-Token"] = self.csrf
        connection_type = (
            http.client.HTTPSConnection
            if self._scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(self._host, self._port, timeout=30)
        try:
            connection.request(
                method, f"{self._prefix}{path}", body=body, headers=request_headers
            )
            response = connection.getresponse()
            content = response.read()
            raw_headers = tuple(response.getheaders())
        finally:
            connection.close()
        response_headers = {name.casefold(): value for name, value in raw_headers}
        set_cookies = tuple(
            value for name, value in raw_headers if name.casefold() == "set-cookie"
        )
        for value in set_cookies:
            first = value.split(";", 1)[0]
            if first.startswith("md_converter_session="):
                session = first.removeprefix("md_converter_session=")
                self._cookie = f"md_converter_session={session}" if session else ""
        return HttpResult(response.status, response_headers, content, set_cookies)

    def login(
        self,
        username: str,
        password: str,
        *,
        origin: str | None = None,
        expected: int = 200,
    ) -> dict[str, Any]:
        headers = {"Origin": origin} if origin is not None else None
        result = self.request(
            "POST",
            "/api/v1/login",
            body=json.dumps({"username": username, "password": password}).encode(),
            content_type="application/json",
            headers=headers,
        )
        expect(result, expected, "login")
        if expected != 200:
            return error_payload(result)
        payload = decode_object(result, "login")
        self.csrf = required_string(payload, "csrf_token", "login")
        cookie = next(
            (
                value
                for value in result.set_cookies
                if value.startswith("md_converter_session=")
            ),
            "",
        )
        lowered = cookie.casefold()
        if not all(
            attribute in lowered for attribute in ("httponly", "secure", "samesite=lax")
        ):
            raise WorkflowFailure("login: secure session cookie contract is missing")
        return payload


def expect(result: HttpResult, expected: int, operation: str) -> None:
    """Require one status without including response content in failures."""

    if result.status != expected:
        code = error_payload(result).get("code", "UNKNOWN")
        raise WorkflowFailure(
            f"{operation}: HTTP {result.status}, expected {expected}, code {code}"
        )


def error_payload(result: HttpResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    return error if isinstance(error, dict) else {}


def decode_object(result: HttpResult, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(result.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure(f"{operation}: invalid JSON response") from error
    if not isinstance(payload, dict):
        raise WorkflowFailure(f"{operation}: JSON object response required")
    return payload


def required_string(payload: dict[str, Any], key: str, operation: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise WorkflowFailure(f"{operation}: missing {key}")
    return value


def json_request(  # noqa: PLR0913 - explicit expected-response contract
    client: ServiceClient,
    method: str,
    path: str,
    payload: dict[str, Any],
    *,
    expected: int,
    operation: str,
    headers: dict[str, str] | None = None,
) -> HttpResult:
    result = client.request(
        method,
        path,
        body=json.dumps(payload).encode(),
        content_type="application/json",
        mutate=True,
        headers=headers,
    )
    expect(result, expected, operation)
    return result


def create_user(admin: ServiceClient, username: str, password: str) -> dict[str, Any]:
    result = json_request(
        admin,
        "POST",
        "/api/v1/admin/users",
        {"username": username, "password": password},
        expected=201,
        operation=f"create {username}",
    )
    return decode_object(result, f"create {username}")


def create_template(
    owner: ServiceClient, template_path: Path, *, run_id: str
) -> dict[str, Any]:
    fields = [
        ("name", f"T21 E2E {run_id}"),
        ("description", "Final-image service workflow"),
        *(("expected_fonts", family) for family in EXPECTED_FONTS),
    ]
    body, content_type = multipart(
        fields,
        [
            (
                "content",
                "template.docx",
                candidate_reference(template_path.read_bytes()),
            )
        ],
    )
    result = owner.request(
        "POST",
        "/api/v1/templates",
        body=body,
        content_type=content_type,
        mutate=True,
    )
    expect(result, 201, "create template")
    return decode_object(result, "create template")


def submit_conversion(  # noqa: PLR0913 - explicit conversion submission contract
    client: ServiceClient,
    template: dict[str, Any],
    output: str,
    source: bytes,
    *,
    idempotency_key: str | None = None,
    filename: str = "source.md",
) -> tuple[dict[str, Any], str]:
    body, content_type = multipart(
        [
            ("template_id", required_string(template, "id", "template")),
            (
                "template_version_id",
                required_string(template, "current_version_id", "template"),
            ),
            ("output", output),
        ],
        [("source", filename, source)],
    )
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    result = client.request(
        "POST",
        "/api/v1/conversions",
        body=body,
        content_type=content_type,
        mutate=True,
        headers=headers,
    )
    expect(result, 202, f"submit {output}")
    job = decode_object(result, f"submit {output}")
    location = result.headers.get("location", "")
    correlation = result.headers.get("x-correlation-id", "")
    try:
        uuid.UUID(correlation)
    except ValueError as error:
        raise WorkflowFailure(
            "submit conversion: correlation UUID is invalid"
        ) from error
    if job.get("correlation_id") != correlation or not location.endswith(
        required_string(job, "id", "conversion")
    ):
        raise WorkflowFailure(
            "submit conversion: location/correlation contract mismatch"
        )
    return job, location


def wait_for_job(
    client: ServiceClient, location: str, *, timeout_seconds: float = 180
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = client.request("GET", location)
        expect(result, 200, "poll conversion")
        job = decode_object(result, "poll conversion")
        if job.get("state") in TERMINAL_STATES:
            return job
        time.sleep(0.25)
    raise WorkflowFailure("poll conversion: terminal state timeout")


def wait_for_running_job(
    client: ServiceClient, location: str, *, timeout_seconds: float = 120
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = client.request("GET", location)
        expect(result, 200, "poll running conversion")
        job = decode_object(result, "poll running conversion")
        if job.get("state") == "running":
            return job
        if job.get("state") in TERMINAL_STATES:
            raise WorkflowFailure("poll running conversion: job terminated too early")
        time.sleep(0.05)
    raise WorkflowFailure("poll running conversion: claim timeout")


def long_mermaid_source(diagrams: int = 8) -> bytes:
    """Build bounded work that keeps the real Mermaid pipeline cancellable."""

    if not 1 <= diagrams <= 20:
        raise ValueError("diagram count must be between 1 and 20")
    blocks = [
        f"## Diagram {index}\n\n```mermaid\nflowchart LR\nA{index}-->B{index}\n```"
        for index in range(1, diagrams + 1)
    ]
    return ("# Cancellation workflow\n\n" + "\n\n".join(blocks) + "\n").encode()


def multipage_markdown(paragraphs: int = 400) -> bytes:
    """Build a bounded source guaranteed to exceed a five-page PDF policy."""

    if not 100 <= paragraphs <= 1_000:
        raise ValueError("paragraph count must be between 100 and 1000")
    paragraph = "Bounded final-image PDF validation text. " * 8
    body = "\n\n".join(f"{index}. {paragraph}" for index in range(paragraphs))
    return f"# PDF output limit\n\n{body}\n".encode()


def validate_result(client: ServiceClient, job: dict[str, Any], output: str) -> None:
    path = f"/api/v1/conversions/{required_string(job, 'id', 'result')}/result"
    result = client.request("GET", path)
    expect(result, 200, f"download {output}")
    if result.headers.get("cache-control") != "private, no-store":
        raise WorkflowFailure(f"download {output}: private cache contract missing")
    embedded_manifest: bytes | None = None
    if output == "docx":
        validate_docx(result.body, "DOCX result")
        unavailable = client.request("GET", f"{path}/manifest")
        expect(unavailable, 409, "DOCX manifest denial")
        return
    if output == "both":
        try:
            with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
                if archive.namelist() != [
                    "document.docx",
                    "document.pdf",
                    "traceability.json",
                ]:
                    raise WorkflowFailure("BOTH result: unexpected archive members")
                validate_docx(archive.read("document.docx"), "BOTH DOCX")
                pdf = archive.read("document.pdf")
                embedded_manifest = archive.read("traceability.json")
        except zipfile.BadZipFile as error:
            raise WorkflowFailure("BOTH result: invalid ZIP") from error
    else:
        pdf = result.body
    if not pdf.startswith(b"%PDF-"):
        raise WorkflowFailure(f"download {output}: invalid PDF")
    manifest_result = client.request("GET", f"{path}/manifest")
    expect(manifest_result, 200, f"download {output} manifest")
    manifest = decode_object(manifest_result, f"download {output} manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("output_format") != "pdf"
        or manifest.get("output_pdf_sha256") != hashlib.sha256(pdf).hexdigest()
        or manifest.get("output_pdf_bytes") != len(pdf)
    ):
        raise WorkflowFailure(f"download {output}: manifest invariants mismatch")
    if embedded_manifest is not None and embedded_manifest != manifest_result.body:
        raise WorkflowFailure("BOTH result: embedded and sidecar manifests differ")


def validate_docx(content: bytes, operation: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not required <= set(archive.namelist()):
                raise WorkflowFailure(f"{operation}: required OpenXML parts missing")
    except zipfile.BadZipFile as error:
        raise WorkflowFailure(f"{operation}: invalid OpenXML archive") from error


def assert_denied(client: ServiceClient, path: str, operation: str) -> None:
    result = client.request("GET", path)
    expect(result, 404, operation)


def scrape_metrics(url: str, *, role: str, forbidden: tuple[str, ...]) -> str:
    path = "" if urlsplit(url).path.rstrip("/").endswith("/metrics") else "/metrics"
    result = ServiceClient(url).request("GET", path)
    expect(result, 200, f"scrape {role} metrics")
    try:
        rendered = result.body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowFailure(f"scrape {role} metrics: invalid text") from error
    names = {line.split("{", 1)[0].split(" ", 1)[0] for line in rendered.splitlines()}
    if not names >= REQUIRED_METRICS:
        raise WorkflowFailure(f"scrape {role} metrics: required gauges missing")
    if any(value and value in rendered for value in forbidden):
        raise WorkflowFailure(f"scrape {role} metrics: identifier/content leak")
    if role == "api" and "md_converter_http_requests_total" not in names:
        raise WorkflowFailure("scrape api metrics: HTTP metrics missing")
    if role == "worker" and "md_converter_http_requests_total" in names:
        raise WorkflowFailure("scrape worker metrics: API-only metrics leaked")
    return rendered


def state_payload(  # noqa: PLR0913 - explicit durable checkpoint schema
    *,
    profile: str,
    owner: str,
    location: str,
    output: str,
    job: dict[str, Any],
    template: dict[str, Any],
    result_sha256: str,
) -> dict[str, str]:
    """Return the complete, content-free restart checkpoint schema."""

    return {
        "schema": "t21-service-checkpoint-v1",
        "profile": profile,
        "owner": owner,
        "location": location,
        "output": output,
        "job_id": required_string(job, "id", "checkpoint"),
        "correlation_id": required_string(job, "correlation_id", "checkpoint"),
        "template_id": required_string(template, "id", "checkpoint"),
        "template_version_id": required_string(
            template, "current_version_id", "checkpoint"
        ),
        "result_sha256": result_sha256,
    }


def write_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_state(path: Path, *, expected_profile: str) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure("checkpoint: unreadable state") from error
    keys = {
        "schema",
        "profile",
        "owner",
        "location",
        "output",
        "job_id",
        "correlation_id",
        "template_id",
        "template_version_id",
        "result_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != keys:
        raise WorkflowFailure("checkpoint: invalid schema")
    if not all(isinstance(raw[key], str) and raw[key] for key in keys):
        raise WorkflowFailure("checkpoint: invalid values")
    if (
        raw["schema"] != "t21-service-checkpoint-v1"
        or raw["profile"] != expected_profile
    ):
        raise WorkflowFailure("checkpoint: profile or version mismatch")
    if raw["output"] not in {"docx", "pdf", "both"}:
        raise WorkflowFailure("checkpoint: invalid output")
    if raw["location"] != f"/api/v1/conversions/{raw['job_id']}":
        raise WorkflowFailure("checkpoint: job location mismatch")
    try:
        uuid.UUID(raw["job_id"])
        uuid.UUID(raw["correlation_id"])
        uuid.UUID(raw["template_id"])
        uuid.UUID(raw["template_version_id"])
    except ValueError as error:
        raise WorkflowFailure("checkpoint: invalid identifier") from error
    if len(raw["result_sha256"]) != 64:
        raise WorkflowFailure("checkpoint: invalid result digest")
    return {key: raw[key] for key in keys}


def exercise_cancellation(
    client: ServiceClient, template: dict[str, Any]
) -> dict[str, Any]:
    """Cancel active real engine work and prove that nothing was published."""

    _submitted, location = submit_conversion(
        client, template, "both", long_mermaid_source()
    )
    running = wait_for_running_job(client, location)
    cancellation = client.request("DELETE", location, mutate=True)
    expect(cancellation, 200, "cancel running conversion")
    terminal = wait_for_job(client, location)
    if terminal.get("state") != "cancelled" or not terminal.get("cancel_requested"):
        raise WorkflowFailure("cancel running conversion: cancelled state missing")
    if terminal.get("id") != running.get("id"):
        raise WorkflowFailure("cancel running conversion: durable identity changed")
    result_path = f"{location}/result"
    expect(client.request("GET", result_path), 409, "cancelled result denial")
    expect(
        client.request("GET", f"{result_path}/manifest"),
        409,
        "cancelled manifest denial",
    )
    return terminal


def exercise_pdf_limit_failure(
    client: ServiceClient, template: dict[str, Any]
) -> dict[str, Any]:
    """Run LibreOffice and require the configured output-page validation failure."""

    _submitted, location = submit_conversion(
        client, template, "pdf", multipage_markdown()
    )
    terminal = wait_for_job(client, location)
    if (
        terminal.get("state") != "failed"
        or terminal.get("error_code") != "pdf_limit_exceeded"
    ):
        raise WorkflowFailure("PDF output limit: deterministic failure missing")
    result_path = f"{location}/result"
    expect(client.request("GET", result_path), 409, "failed PDF result denial")
    expect(
        client.request("GET", f"{result_path}/manifest"),
        409,
        "failed PDF manifest denial",
    )
    return terminal


def exercise(arguments: argparse.Namespace) -> None:  # noqa: PLR0912, PLR0915
    run_id = uuid.uuid4().hex[:10]
    alice_password = secrets.token_urlsafe(24)
    alice_reset_password = secrets.token_urlsafe(24)
    bob_password = secrets.token_urlsafe(24)
    source_marker = f"t21-{run_id}-content-marker"
    source = f"# Final image service E2E\n\n{source_marker}\n".encode()
    admin = ServiceClient(arguments.base_url)
    hostile = ServiceClient(arguments.base_url)
    hostile_error = hostile.login(
        arguments.admin_username,
        arguments.admin_password,
        origin="https://attacker.invalid",
        expected=403,
    )
    if hostile_error.get("code") != "LOGIN_ORIGIN_INVALID":
        raise WorkflowFailure("hostile login: stable origin error missing")
    admin_login = admin.login(arguments.admin_username, arguments.admin_password)
    admin_id = required_string(admin_login["user"], "id", "admin login")
    session = admin.request("GET", "/api/v1/session")
    expect(session, 200, "admin session")

    alice_name = f"e2e-alice-{run_id}"
    bob_name = f"e2e-bob-{run_id}"
    alice_user = create_user(admin, alice_name, alice_password)
    bob_user = create_user(admin, bob_name, bob_password)
    alice = ServiceClient(arguments.base_url)
    bob = ServiceClient(arguments.base_url)
    alice.login(alice_name, alice_password)
    bob.login(bob_name, bob_password)

    json_request(
        admin,
        "POST",
        f"/api/v1/admin/users/{alice_user['id']}/password",
        {"password": alice_reset_password},
        expected=204,
        operation="reset Alice password",
    )
    expect(alice.request("GET", "/api/v1/session"), 401, "revoke Alice session")
    ServiceClient(arguments.base_url).login(alice_name, alice_password, expected=401)
    alice = ServiceClient(arguments.base_url)
    alice.login(alice_name, alice_reset_password)

    json_request(
        admin,
        "PATCH",
        f"/api/v1/admin/users/{bob_user['id']}/active",
        {"active": False},
        expected=200,
        operation="deactivate Bob",
    )
    expect(bob.request("GET", "/api/v1/session"), 401, "revoke Bob session")
    ServiceClient(arguments.base_url).login(bob_name, bob_password, expected=401)
    json_request(
        admin,
        "PATCH",
        f"/api/v1/admin/users/{bob_user['id']}/active",
        {"active": True},
        expected=200,
        operation="reactivate Bob",
    )
    bob = ServiceClient(arguments.base_url)
    bob.login(bob_name, bob_password)

    template = create_template(alice, arguments.template, run_id=run_id)
    template_id = required_string(template, "id", "template")
    visible = bob.request("GET", f"/api/v1/templates/{template_id}")
    expect(visible, 200, "Bob template visibility")
    visible_etag = visible.headers.get("etag", "")
    if not visible_etag:
        raise WorkflowFailure("Bob template visibility: ETag missing")
    downloaded = bob.request("GET", f"/api/v1/templates/{template_id}/content")
    expect(downloaded, 200, "Bob template download")
    validate_docx(downloaded.body, "visible template")
    forbidden_update = json_request(
        bob,
        "PATCH",
        f"/api/v1/templates/{template_id}",
        {"name": "Forbidden", "description": "Forbidden"},
        expected=403,
        operation="Bob template mutation denial",
        headers={"If-Match": visible_etag},
    )
    if error_payload(forbidden_update).get("code") != "FORBIDDEN":
        raise WorkflowFailure("Bob template mutation denial: stable error missing")
    template_snapshot = alice.request("GET", f"/api/v1/templates/{template_id}")
    expect(template_snapshot, 200, "template revision")
    etag = template_snapshot.headers.get("etag", "")
    admin_update = admin.request(
        "PATCH",
        f"/api/v1/templates/{template_id}",
        body=json.dumps(
            {"name": f"T21 E2E {run_id}", "description": "Admin-reviewed E2E template"}
        ).encode(),
        content_type="application/json",
        mutate=True,
        headers={"If-Match": etag},
    )
    expect(admin_update, 200, "administrator template intervention")

    completed: dict[str, dict[str, Any]] = {}
    locations: dict[str, str] = {}
    for output in ("docx", "pdf", "both"):
        _job, location = submit_conversion(alice, template, output, source)
        terminal = wait_for_job(alice, location)
        if terminal.get("state") != "succeeded":
            raise WorkflowFailure(f"{output} conversion: did not succeed")
        validate_result(alice, terminal, output)
        completed[output], locations[output] = terminal, location

    exercise_cancellation(alice, template)
    exercise_pdf_limit_failure(alice, template)

    replay_key = f"t21-{run_id}-replay"
    replay_first, replay_location = submit_conversion(
        alice, template, "docx", source, idempotency_key=replay_key
    )
    replay_second, replay_second_location = submit_conversion(
        alice,
        template,
        "docx",
        source,
        idempotency_key=replay_key,
        filename="replayed-name.md",
    )
    if (
        replay_first["id"] != replay_second["id"]
        or replay_location != replay_second_location
    ):
        raise WorkflowFailure("idempotency replay: job identity changed")
    conflict_body, conflict_type = multipart(
        [
            ("template_id", template_id),
            ("template_version_id", str(template["current_version_id"])),
            ("output", "docx"),
        ],
        [("source", "source.md", b"# Different idempotency payload")],
    )
    conflict = alice.request(
        "POST",
        "/api/v1/conversions",
        body=conflict_body,
        content_type=conflict_type,
        mutate=True,
        headers={"Idempotency-Key": replay_key},
    )
    expect(conflict, 409, "idempotency conflict")
    if error_payload(conflict).get("code") != "CONVERSION_CONFLICT":
        raise WorkflowFailure("idempotency conflict: stable error missing")

    alice_job_id = required_string(completed["docx"], "id", "Alice job")
    assert_denied(bob, locations["docx"], "cross-owner job denial")
    assert_denied(
        bob,
        f"/api/v1/conversions/{alice_job_id}/result",
        "cross-owner result denial",
    )
    assert_denied(
        bob,
        f"/api/v1/conversions/{alice_job_id}/result/manifest",
        "cross-owner manifest denial",
    )
    expect(bob.request("GET", "/api/v1/audit"), 403, "non-admin audit denial")

    with ThreadPoolExecutor(max_workers=2) as pool:
        alice_future = pool.submit(
            submit_conversion, alice, template, "docx", b"# Concurrent Alice"
        )
        bob_future = pool.submit(
            submit_conversion, bob, template, "pdf", b"# Concurrent Bob"
        )
        alice_concurrent, alice_location = alice_future.result()
        bob_concurrent, bob_location = bob_future.result()
    alice_terminal = wait_for_job(alice, alice_location)
    bob_terminal = wait_for_job(bob, bob_location)
    if (
        alice_terminal.get("state") != "succeeded"
        or bob_terminal.get("state") != "succeeded"
    ):
        raise WorkflowFailure("concurrent conversions: terminal success missing")
    validate_result(alice, alice_terminal, "docx")
    validate_result(bob, bob_terminal, "pdf")
    assert_denied(alice, bob_location, "Alice-to-Bob job isolation")
    assert_denied(bob, alice_location, "Bob-to-Alice job isolation")
    if alice_concurrent["owner_id"] == bob_concurrent["owner_id"]:
        raise WorkflowFailure("concurrent conversions: owners were not isolated")

    audit_result = admin.request("GET", "/api/v1/audit?limit=100")
    expect(audit_result, 200, "administrator audit read")
    audits = json.loads(audit_result.body)
    if not isinstance(audits, list):
        raise WorkflowFailure("administrator audit read: list required")
    timestamps = [str(record.get("created_at", "")) for record in audits]
    if timestamps != sorted(timestamps, reverse=True):
        raise WorkflowFailure("administrator audit read: ordering is not deterministic")
    account_operations = {
        record.get("operation")
        for record in audits
        if record.get("target_type") == "user"
    }
    if (
        not {
            "user_create",
            "user_password_reset",
            "user_deactivate",
            "user_reactivate",
        }
        <= account_operations
    ):
        raise WorkflowFailure("administrator audit read: account operations missing")
    template_records = [
        record
        for record in audits
        if record.get("target_type") == "template"
        and record.get("target_id") == template_id
    ]
    if not any(record.get("administrator_intervention") for record in template_records):
        raise WorkflowFailure("administrator audit read: template intervention missing")
    serialized_audits = json.dumps(audits, sort_keys=True)
    forbidden_values = (
        source_marker,
        alice_password,
        alice_reset_password,
        bob_password,
        arguments.admin_password,
    )
    if any(value in serialized_audits for value in forbidden_values):
        raise WorkflowFailure("administrator audit read: content or credential leak")

    api_metrics_url = arguments.api_metrics_url or arguments.base_url
    scrape_metrics(
        api_metrics_url,
        role="api",
        forbidden=(source_marker, alice_name, bob_name, alice_job_id, admin_id),
    )
    if arguments.worker_metrics_url:
        scrape_metrics(
            arguments.worker_metrics_url,
            role="worker",
            forbidden=(source_marker, alice_name, bob_name, alice_job_id, admin_id),
        )
    if arguments.state_file:
        write_state(
            arguments.state_file,
            state_payload(
                profile=arguments.profile,
                owner=alice_name,
                location=locations["both"],
                output="both",
                job=completed["both"],
                template=template,
                result_sha256=result_digest(alice, completed["both"]),
            ),
        )

    logout = alice.request("POST", "/api/v1/logout", mutate=True)
    expect(logout, 204, "Alice logout")
    expect(alice.request("GET", "/api/v1/session"), 401, "Alice logout revocation")


def result_digest(client: ServiceClient, job: dict[str, Any]) -> str:
    job_id = required_string(job, "id", "result digest")
    result = client.request("GET", f"/api/v1/conversions/{job_id}/result")
    expect(result, 200, "result digest")
    return hashlib.sha256(result.body).hexdigest()


def require_template_audit(client: ServiceClient, template_id: str) -> None:
    result = client.request("GET", "/api/v1/audit?limit=100")
    expect(result, 200, "checkpoint audit")
    records = json.loads(result.body)
    if not isinstance(records, list) or not any(
        record.get("target_type") == "template"
        and record.get("target_id") == template_id
        and record.get("operation") == "create"
        for record in records
    ):
        raise WorkflowFailure("checkpoint audit: template creation record missing")


def checkpoint(arguments: argparse.Namespace) -> None:
    client = ServiceClient(arguments.base_url)
    client.login(arguments.admin_username, arguments.admin_password)
    template = create_template(client, arguments.template, run_id=uuid.uuid4().hex[:10])
    source = b"# Restart recovery checkpoint\n\nDurable final-image job.\n"
    _job, location = submit_conversion(client, template, arguments.output, source)
    terminal = wait_for_job(client, location)
    if terminal.get("state") != "succeeded":
        raise WorkflowFailure("checkpoint: conversion did not succeed")
    validate_result(client, terminal, arguments.output)
    require_template_audit(client, str(template["id"]))
    write_state(
        arguments.state_file,
        state_payload(
            profile=arguments.profile,
            owner=arguments.admin_username,
            location=location,
            output=arguments.output,
            job=terminal,
            template=template,
            result_sha256=result_digest(client, terminal),
        ),
    )


def verify_checkpoint(arguments: argparse.Namespace) -> None:
    state = read_state(arguments.state_file, expected_profile=arguments.profile)
    client = ServiceClient(arguments.base_url)
    client.login(arguments.admin_username, arguments.admin_password)
    job = wait_for_job(client, state["location"])
    if job.get("state") != "succeeded":
        raise WorkflowFailure("checkpoint recovery: job did not succeed")
    if (
        job.get("id") != state["job_id"]
        or job.get("correlation_id") != state["correlation_id"]
    ):
        raise WorkflowFailure("checkpoint recovery: durable identity changed")
    validate_result(client, job, state["output"])
    if result_digest(client, job) != state["result_sha256"]:
        raise WorkflowFailure("checkpoint recovery: result bytes changed")
    require_template_audit(client, state["template_id"])


def recovery_payload(
    *, profile: str, location: str, output: str, job: dict[str, Any]
) -> dict[str, str]:
    """Return content-free evidence for a leased job before worker interruption."""

    return {
        "schema": "t21-service-recovery-v1",
        "profile": profile,
        "location": location,
        "output": output,
        "job_id": required_string(job, "id", "recovery"),
        "correlation_id": required_string(job, "correlation_id", "recovery"),
        "attempt": str(job.get("attempt", "")),
    }


def read_recovery_state(path: Path, *, expected_profile: str) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure("recovery: unreadable state") from error
    keys = {
        "schema",
        "profile",
        "location",
        "output",
        "job_id",
        "correlation_id",
        "attempt",
    }
    if not isinstance(raw, dict) or set(raw) != keys:
        raise WorkflowFailure("recovery: invalid schema")
    if not all(isinstance(raw[key], str) and raw[key] for key in keys):
        raise WorkflowFailure("recovery: invalid values")
    if raw["schema"] != "t21-service-recovery-v1" or raw["profile"] != expected_profile:
        raise WorkflowFailure("recovery: profile or version mismatch")
    if raw["output"] not in {"docx", "pdf", "both"}:
        raise WorkflowFailure("recovery: invalid output")
    if raw["location"] != f"/api/v1/conversions/{raw['job_id']}":
        raise WorkflowFailure("recovery: job location mismatch")
    try:
        uuid.UUID(raw["job_id"])
        uuid.UUID(raw["correlation_id"])
        int(raw["attempt"])
    except ValueError as error:
        raise WorkflowFailure("recovery: invalid durable identity") from error
    return {key: raw[key] for key in keys}


def submit_recovery(arguments: argparse.Namespace) -> None:
    client = ServiceClient(arguments.base_url)
    client.login(arguments.admin_username, arguments.admin_password)
    template = create_template(client, arguments.template, run_id=uuid.uuid4().hex[:10])
    source = b"# Worker recovery\n\n```mermaid\nflowchart LR\nA-->B\n```\n"
    _job, location = submit_conversion(client, template, arguments.output, source)
    deadline = time.monotonic() + arguments.timeout_seconds
    while time.monotonic() < deadline:
        result = client.request("GET", location)
        expect(result, 200, "recovery poll")
        snapshot = decode_object(result, "recovery poll")
        if snapshot.get("state") == "running":
            write_state(
                arguments.state_file,
                recovery_payload(
                    profile=arguments.profile,
                    location=location,
                    output=arguments.output,
                    job=snapshot,
                ),
            )
            return
        if snapshot.get("state") in TERMINAL_STATES:
            raise WorkflowFailure("recovery: job terminated before worker interruption")
        time.sleep(0.1)
    raise WorkflowFailure("recovery: job was not claimed before timeout")


def verify_recovery(arguments: argparse.Namespace) -> None:
    state = read_recovery_state(
        arguments.state_file, expected_profile=arguments.profile
    )
    client = ServiceClient(arguments.base_url)
    client.login(arguments.admin_username, arguments.admin_password)
    job = wait_for_job(
        client, state["location"], timeout_seconds=arguments.timeout_seconds
    )
    if job.get("state") != "succeeded":
        raise WorkflowFailure("recovery: interrupted job did not succeed")
    if (
        job.get("id") != state["job_id"]
        or job.get("correlation_id") != state["correlation_id"]
    ):
        raise WorkflowFailure("recovery: durable identity changed")
    if int(job.get("attempt", 0)) <= int(state["attempt"]):
        raise WorkflowFailure("recovery: job was not reclaimed after interruption")
    validate_result(client, job, state["output"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "exercise",
            "checkpoint",
            "verify-checkpoint",
            "submit-recovery",
            "verify-recovery",
        ),
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--profile", required=True, choices=("standalone", "distributed")
    )
    parser.add_argument("--template", type=Path)
    parser.add_argument("--api-metrics-url")
    parser.add_argument("--worker-metrics-url")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", choices=("docx", "pdf", "both"), default="docx")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--admin-username",
        default=os.getenv(
            "MD_CONVERTER_E2E_ADMIN_USERNAME",
            os.getenv("T21_ADMIN_USERNAME", "e2e-admin"),
        ),
    )
    parser.add_argument(
        "--admin-password",
        default=os.getenv(
            "MD_CONVERTER_E2E_ADMIN_PASSWORD",
            os.getenv("T21_ADMIN_PASSWORD", "e2e-admin-password"),
        ),
    )
    return parser


def validate_arguments(
    parser: argparse.ArgumentParser, arguments: argparse.Namespace
) -> None:
    if arguments.command in {"exercise", "checkpoint", "submit-recovery"}:
        if arguments.template is None:
            parser.error("--template is required for this command")
        if not arguments.template.is_file():
            parser.error("--template must name a readable file")
    if arguments.command != "exercise" and arguments.state_file is None:
        parser.error("--state-file is required for checkpoint and recovery commands")
    if arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    for name in ("base_url", "api_metrics_url", "worker_metrics_url"):
        value = getattr(arguments, name)
        if value is None:
            continue
        try:
            ServiceClient(value)
        except ValueError as error:
            parser.error(f"--{name.replace('_', '-')} is invalid: {error}")


def write_failure_artifact(
    artifact_dir: Path | None, *, profile: str, message: str
) -> None:
    if artifact_dir is None:
        return
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "t21-service-failure-v1",
        "profile": profile,
        "failed_at": datetime.now(UTC).isoformat(),
        "message": message[:300],
    }
    path = artifact_dir / "service-failure.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    validate_arguments(parser, arguments)
    try:
        if arguments.command == "exercise":
            exercise(arguments)
        elif arguments.command == "checkpoint":
            checkpoint(arguments)
        elif arguments.command == "verify-checkpoint":
            verify_checkpoint(arguments)
        elif arguments.command == "submit-recovery":
            submit_recovery(arguments)
        else:
            verify_recovery(arguments)
    except WorkflowFailure as error:
        message = str(error)
        write_failure_artifact(
            arguments.artifact_dir, profile=arguments.profile, message=message
        )
        print(f"service E2E failed: {message}", file=sys.stderr)
        return 1
    except Exception as error:
        message = f"unexpected {type(error).__name__}"
        write_failure_artifact(
            arguments.artifact_dir, profile=arguments.profile, message=message
        )
        print(f"service E2E failed: {message}", file=sys.stderr)
        return 1
    print(f"service E2E {arguments.command} passed for {arguments.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
