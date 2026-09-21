"""Exercise reverse recovery and serialized execution against final images."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from markweave.config import Settings, StorageProfile
from markweave.persistence.schema import ReversionAttemptRow
from markweave.persistence.sql import create_database_engine, standalone_database_url

_DIAGNOSTICS_EXECUTION = __name__ == "__main__" and sys.argv[1:2] == ["diagnostics"]
if _DIAGNOSTICS_EXECUTION:  # pragma: no cover - final-image projected SELECT

    class WorkflowFailure(RuntimeError):
        """A bounded diagnostics failure without configuration disclosure."""

else:
    from scripts.container.api_workflow_smoke import multipart

    try:
        from tests.e2e.service_workflow import (
            ServiceClient,
            WorkflowFailure,
            create_user,
            decode_object,
            expect,
        )
    except ModuleNotFoundError:  # pragma: no cover - alternate executable layout
        from service_workflow import (  # type: ignore[no-redef]
            ServiceClient,
            WorkflowFailure,
            create_user,
            decode_object,
            expect,
        )

_SCHEMA = "t73-reverse-lifecycle-v1"
_DIAGNOSTICS_SCHEMA = "t73-reverse-attempt-diagnostics-v1"
_RESULT_RECEIPT_SCHEMA = "t73-reverse-lifecycle-result-v1"
_PASSWORD = "T73-lifecycle-fixture-password"  # noqa: S105 - E2E fixture
_SOURCE = Path("spikes/anydoc/corpus/docx/text.docx")
_SUBMISSION_COUNT = 4
_SCENARIOS = ("worker-restart", "broker-restart")
_MAX_DIAGNOSTIC_ATTEMPTS = 32


def _username(profile: str, scenario: str) -> str:
    return f"t73-lifecycle-{profile}-{scenario}"


def _normalized_source() -> bytes:
    if not _SOURCE.is_file():
        raise WorkflowFailure("reverse lifecycle corpus fixture is unavailable")
    rebuilt = io.BytesIO()
    with (
        zipfile.ZipFile(_SOURCE) as source,
        zipfile.ZipFile(rebuilt, "w", compression=zipfile.ZIP_STORED) as output,
    ):
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "word/_rels/document.xml.rels":
                content = content.replace(
                    b'Target="../../fixture-src/sibling.odt"',
                    b'Target="https://example.test/sibling"',
                )
            output.writestr(info, content)
    return rebuilt.getvalue()


def _owner(base_url: str, profile: str, scenario: str) -> ServiceClient:
    owner = ServiceClient(base_url)
    owner.login(_username(profile, scenario), _PASSWORD)
    return owner


def _submit(client: ServiceClient, source: bytes, key: str) -> dict[str, Any]:
    body, content_type = multipart([], [("source", "lifecycle.docx", source)])
    response = client.request(
        "POST",
        "/api/v1/reversions",
        body=body,
        content_type=content_type,
        mutate=True,
        headers={"Idempotency-Key": key},
    )
    expect(response, 202, "reverse lifecycle submission")
    return decode_object(response, "reverse lifecycle submission")


def _submit_forward(client: ServiceClient, profile: str, scenario: str) -> str:
    body, content_type = multipart(
        [("output", "docx")],
        [("source", "fair-progress.md", b"# Forward progress during reverse work\n")],
    )
    response = client.request(
        "POST",
        "/api/v1/conversions",
        body=body,
        content_type=content_type,
        mutate=True,
        headers={"Idempotency-Key": f"t73-lifecycle-{profile}-{scenario}-forward"},
    )
    expect(response, 202, "submit forward job during reverse work")
    return _job_id(decode_object(response, "submit forward job during reverse work"))


def _job_id(payload: dict[str, Any]) -> str:
    try:
        return str(UUID(str(payload["id"])))
    except (KeyError, TypeError, ValueError) as error:
        raise WorkflowFailure("reverse lifecycle job identity is invalid") from error


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 65_536:
        raise WorkflowFailure("reverse lifecycle state exceeds its byte limit")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _read_state(
    path: Path, profile: str | None = None, scenario: str | None = None
) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure("reverse lifecycle state is unreadable") from error
    expected = {
        "schema",
        "profile",
        "scenario",
        "owner",
        "job_ids",
        "recovery_job_id",
        "shared_job_id",
        "forward_job_id",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkflowFailure("reverse lifecycle state schema is invalid")
    if (
        value.get("schema") != _SCHEMA
        or (profile is not None and value.get("profile") != profile)
        or (scenario is not None and value.get("scenario") != scenario)
    ):
        raise WorkflowFailure("reverse lifecycle state identity is invalid")
    jobs = value.get("job_ids")
    if (
        not isinstance(jobs, list)
        or len(jobs) != _SUBMISSION_COUNT
        or len(set(jobs)) != len(jobs)
    ):
        raise WorkflowFailure("reverse lifecycle state job set is invalid")
    try:
        validated = [str(UUID(job)) for job in jobs]
        recovery = str(UUID(value["recovery_job_id"]))
        shared = str(UUID(value["shared_job_id"]))
        str(UUID(value["forward_job_id"]))
    except (TypeError, ValueError) as error:
        raise WorkflowFailure(
            "reverse lifecycle state contains an invalid UUID"
        ) from error
    if (
        recovery not in validated
        or shared not in validated
        or shared == recovery
        or value.get("owner")
        != _username(str(value["profile"]), str(value["scenario"]))
    ):
        raise WorkflowFailure("reverse lifecycle state binding is invalid")
    return value


def prepare(base_url: str, profile: str, scenario: str, state_file: Path) -> None:
    """Seed one ordered recovery target plus a concurrent queue while work is held."""
    admin = ServiceClient(base_url)
    admin.login("e2e-admin", "e2e-admin-password")
    create_user(admin, _username(profile, scenario), _PASSWORD)
    clients = tuple(_owner(base_url, profile, scenario) for _ in range(7))
    source = _normalized_source()
    recovery_job_id = _job_id(
        _submit(clients[0], source, f"t73-lifecycle-{profile}-{scenario}-recovery")
    )
    shared_key = f"t73-lifecycle-{profile}-{scenario}-shared"
    keys = (shared_key,) * 4 + tuple(
        f"t73-lifecycle-{profile}-{scenario}-distinct-{index}" for index in range(2)
    )
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        futures = tuple(
            pool.submit(_submit, client, source, key)
            for client, key in zip(clients[1:], keys, strict=True)
        )
        submissions = tuple(future.result() for future in futures)
    shared_ids = {_job_id(value) for value in submissions[:4]}
    if len(shared_ids) != 1:
        raise WorkflowFailure("concurrent idempotent submissions created multiple jobs")
    job_ids = (
        recovery_job_id,
        *tuple(dict.fromkeys(_job_id(value) for value in submissions)),
    )
    if len(job_ids) != _SUBMISSION_COUNT:
        raise WorkflowFailure(
            "concurrent distinct submissions did not create four jobs"
        )
    forward_job_id = _submit_forward(clients[0], profile, scenario)

    owner = clients[0]
    listed = owner.request("GET", "/api/v1/reversions?offset=0&limit=100")
    expect(listed, 200, "reverse lifecycle list")
    items = decode_object(listed, "reverse lifecycle list").get("items")
    if not isinstance(items, list) or {
        item.get("id") for item in items if isinstance(item, dict)
    }.intersection(job_ids) != set(job_ids):
        raise WorkflowFailure("reverse lifecycle owner list omitted a synthetic job")
    target_response = owner.request("GET", f"/api/v1/reversions/{recovery_job_id}")
    expect(target_response, 200, "observe held reverse recovery target")
    target = decode_object(target_response, "observe held reverse recovery target")
    if target.get("state") != "queued" or target.get("attempt") != 0:
        raise WorkflowFailure("reverse execution was not held before lifecycle prepare")
    _write_json(
        state_file,
        {
            "schema": _SCHEMA,
            "profile": profile,
            "scenario": scenario,
            "owner": _username(profile, scenario),
            "job_ids": list(job_ids),
            "recovery_job_id": recovery_job_id,
            "shared_job_id": next(iter(shared_ids)),
            "forward_job_id": forward_job_id,
        },
    )


def _wait_success(client: ServiceClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.request("GET", f"/api/v1/reversions/{job_id}")
        expect(response, 200, "verify reverse lifecycle job")
        job = decode_object(response, "verify reverse lifecycle job")
        state = job.get("state")
        if state == "succeeded":
            return job
        if state in {"failed", "cancelled", "expired"}:
            raise WorkflowFailure(f"reverse lifecycle job ended as {state}")
        time.sleep(0.1)
    raise WorkflowFailure("reverse lifecycle recovery timed out")


def _wait_forward_success(client: ServiceClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    path = f"/api/v1/conversions/{job_id}"
    while time.monotonic() < deadline:
        response = client.request("GET", path)
        expect(response, 200, "verify forward progress during reverse recovery")
        job = decode_object(response, "verify forward progress during reverse recovery")
        state = job.get("state")
        if state == "succeeded":
            return job
        if state in {"failed", "cancelled", "expired"}:
            raise WorkflowFailure(f"concurrent forward job ended as {state}")
        time.sleep(0.1)
    raise WorkflowFailure("concurrent forward progress timed out")


def _download(client: ServiceClient, job_id: str) -> bytes:
    response = client.request("GET", f"/api/v1/reversions/{job_id}/result")
    expect(response, 200, "download reverse lifecycle result")
    if (
        response.headers.get("content-type") != "application/zip"
        or "private" not in response.headers.get("cache-control", "")
        or "no-store" not in response.headers.get("cache-control", "")
        or response.headers.get("x-content-type-options") != "nosniff"
    ):
        raise WorkflowFailure("reverse lifecycle download headers are unsafe")
    return response.body


def _inspect_package(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if archive.namelist() != [
                "document.md",
                "assets/image-0001.png",
                "manifest.json",
            ]:
                raise WorkflowFailure("reverse lifecycle ZIP order is invalid")
            markdown = archive.read("document.md")
            image = archive.read("assets/image-0001.png")
            manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, ValueError, zipfile.BadZipFile) as error:
        raise WorkflowFailure("reverse lifecycle result package is invalid") from error
    if (
        b"![tiny red dot](assets/image-0001.png)" not in markdown
        or not image.startswith(b"\x89PNG\r\n\x1a\n")
        or manifest.get("source") != {"family": "word", "detected_format": "docx"}
        or manifest.get("result", {}).get("asset_count") != 1
    ):
        raise WorkflowFailure("reverse lifecycle result integrity is invalid")


def verify(  # noqa: PLR0913, PLR0917 - explicit final-image evidence inputs
    base_url: str,
    profile: str,
    scenario: str,
    state_file: Path,
    diagnostics_file: Path | None,
    result_receipt: Path | None,
) -> None:
    """Verify recovered publication, deterministic output, and persisted fencing."""
    state = _read_state(state_file, profile, scenario)
    if result_receipt is not None and diagnostics_file is None:
        raise WorkflowFailure("a result receipt requires persisted fencing evidence")
    owner = _owner(base_url, profile, scenario)
    jobs = {job_id: _wait_success(owner, job_id) for job_id in state["job_ids"]}
    forward = _wait_forward_success(owner, state["forward_job_id"])
    recovery = jobs[state["recovery_job_id"]]
    if not isinstance(recovery.get("attempt"), int) or recovery["attempt"] < 2:
        raise WorkflowFailure(
            "the interrupted reverse job did not exercise lease recovery"
        )
    forward_updated = _timestamp(forward.get("updated_at"), "forward updated_at")
    reverse_updates = [
        _timestamp(job.get("updated_at"), "reverse updated_at") for job in jobs.values()
    ]
    if not any(forward_updated < updated for updated in reverse_updates):
        raise WorkflowFailure(
            "forward work made no progress before the reverse queue drained"
        )
    packages: dict[str, bytes] = {}
    for job_id in state["job_ids"]:
        first = _download(owner, job_id)
        second = _download(owner, job_id)
        if first != second:
            raise WorkflowFailure(
                "one immutable reverse result changed between downloads"
            )
        _inspect_package(first)
        packages[job_id] = first
    expected_package = packages[state["recovery_job_id"]]
    if any(content != expected_package for content in packages.values()):
        raise WorkflowFailure(
            "equivalent reverse jobs produced non-deterministic packages"
        )

    replay = _submit(
        owner,
        _normalized_source(),
        f"t73-lifecycle-{profile}-{scenario}-shared",
    )
    if _job_id(replay) != state["shared_job_id"]:
        raise WorkflowFailure("post-restart idempotent replay changed job identity")
    if diagnostics_file is not None:
        _validate_diagnostics(diagnostics_file, state, jobs)
    if result_receipt is not None:
        _write_json(
            result_receipt,
            {
                "schema": _RESULT_RECEIPT_SCHEMA,
                "profile": profile,
                "scenario": scenario,
                "recovery_job_id": state["recovery_job_id"],
                "sha256": sha256(expected_package).hexdigest(),
            },
        )


def diagnostics(
    state_file: Path, output: Path, *, wait_for_recovery_attempt: bool = False
) -> None:
    """Project content-free attempt fencing evidence for synthetic job UUIDs only."""
    state = _read_state(state_file)
    settings = Settings()
    if settings.storage_profile is StorageProfile.STANDALONE:
        if settings.standalone_data_directory is None:
            raise WorkflowFailure("standalone diagnostics configuration is incomplete")
        database_url = standalone_database_url(settings.standalone_data_directory)
    else:
        if settings.distributed_database_url is None:
            raise WorkflowFailure("distributed diagnostics configuration is incomplete")
        database_url = settings.distributed_database_url.get_secret_value()
    engine = create_database_engine(database_url, timeout_seconds=2)
    deadline = time.monotonic() + 30
    try:
        statement = (
            select(
                ReversionAttemptRow.job_id,
                ReversionAttemptRow.attempt_id,
                ReversionAttemptRow.attempt_number,
                ReversionAttemptRow.leased_at,
                ReversionAttemptRow.lease_expires_at,
                ReversionAttemptRow.create_sequence,
                ReversionAttemptRow.create_intent_at,
                ReversionAttemptRow.unit_id,
                ReversionAttemptRow.proof_id,
                ReversionAttemptRow.proof_unit_id,
                ReversionAttemptRow.proof_recorded_at,
                ReversionAttemptRow.proof_acknowledged_at,
            )
            .where(ReversionAttemptRow.job_id.in_(state["job_ids"]))
            .order_by(ReversionAttemptRow.leased_at, ReversionAttemptRow.attempt_id)
            .limit(_MAX_DIAGNOSTIC_ATTEMPTS + 1)
        )
        while True:
            with engine.connect() as connection:
                rows = connection.execute(statement).all()
            if not wait_for_recovery_attempt or any(
                row.job_id == state["recovery_job_id"] for row in rows
            ):
                break
            if time.monotonic() >= deadline:
                raise WorkflowFailure(
                    "reverse lifecycle recovery attempt was not claimed"
                )
            time.sleep(0.01)
    finally:
        engine.dispose()
    if len(rows) > _MAX_DIAGNOSTIC_ATTEMPTS:
        raise WorkflowFailure("reverse lifecycle diagnostics exceed the attempt limit")

    def value(item: object) -> object:
        return item.isoformat() if isinstance(item, datetime) else item

    projected = [
        {key: value(item) for key, item in row._mapping.items()} for row in rows
    ]
    _write_json(
        output,
        {
            "schema": _DIAGNOSTICS_SCHEMA,
            "profile": state["profile"],
            "scenario": state["scenario"],
            "job_ids": state["job_ids"],
            "attempts": projected,
        },
    )


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise WorkflowFailure(f"reverse diagnostics {field} is invalid")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise WorkflowFailure(f"reverse diagnostics {field} is invalid") from error


def _attempt_interval(
    row: dict[str, Any], identifiers: set[str], sequences: set[int]
) -> tuple[datetime, datetime]:
    try:
        attempt_id = str(UUID(row["attempt_id"]))
    except (TypeError, ValueError) as error:
        raise WorkflowFailure("reverse diagnostic attempt UUID is invalid") from error
    sequence = row["create_sequence"]
    if (
        attempt_id in identifiers
        or not isinstance(sequence, int)
        or sequence <= 0
        or sequence in sequences
    ):
        raise WorkflowFailure("reverse diagnostic attempt identity is not unique")
    identifiers.add(attempt_id)
    sequences.add(sequence)
    leased = _timestamp(row["leased_at"], "leased_at")
    if row["create_intent_at"] is None:
        if any(
            row[name] is not None
            for name in (
                "unit_id",
                "proof_id",
                "proof_unit_id",
                "proof_recorded_at",
                "proof_acknowledged_at",
            )
        ):
            raise WorkflowFailure("pre-create attempt contains broker evidence")
        safe_end = _timestamp(row["lease_expires_at"], "lease_expires_at")
    else:
        _timestamp(row["create_intent_at"], "create_intent_at")
        if row["unit_id"] != row["proof_unit_id"] or any(
            row[name] is None
            for name in (
                "unit_id",
                "proof_id",
                "proof_unit_id",
                "proof_recorded_at",
                "proof_acknowledged_at",
            )
        ):
            raise WorkflowFailure("broker attempt lacks exact termination proof")
        recorded = _timestamp(row["proof_recorded_at"], "proof_recorded_at")
        acknowledged = _timestamp(row["proof_acknowledged_at"], "proof_acknowledged_at")
        if acknowledged < recorded:
            raise WorkflowFailure("termination proof acknowledgement is not monotonic")
        safe_end = recorded
    if safe_end < leased:
        raise WorkflowFailure("reverse attempt safety interval is invalid")
    return leased, safe_end


def _validate_diagnostics(
    path: Path, state: dict[str, Any], jobs: dict[str, dict[str, Any]]
) -> None:
    try:
        evidence = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure("reverse lifecycle diagnostics are unreadable") from error
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"schema", "profile", "scenario", "job_ids", "attempts"}
        or evidence.get("schema") != _DIAGNOSTICS_SCHEMA
        or evidence.get("profile") != state["profile"]
        or evidence.get("scenario") != state["scenario"]
        or evidence.get("job_ids") != state["job_ids"]
        or not isinstance(evidence.get("attempts"), list)
    ):
        raise WorkflowFailure("reverse lifecycle diagnostics schema is invalid")
    expected_fields = {
        "job_id",
        "attempt_id",
        "attempt_number",
        "leased_at",
        "lease_expires_at",
        "create_sequence",
        "create_intent_at",
        "unit_id",
        "proof_id",
        "proof_unit_id",
        "proof_recorded_at",
        "proof_acknowledged_at",
    }
    attempts = evidence["attempts"]
    if any(
        not isinstance(row, dict) or set(row) != expected_fields for row in attempts
    ):
        raise WorkflowFailure("reverse lifecycle diagnostics projection is invalid")
    grouped: dict[str, list[dict[str, Any]]] = {
        job_id: [] for job_id in state["job_ids"]
    }
    identifiers: set[str] = set()
    sequences: set[int] = set()
    intervals: list[tuple[datetime, datetime]] = []
    for row in attempts:
        job_id = row["job_id"]
        if job_id not in grouped:
            raise WorkflowFailure("reverse diagnostics include another job")
        intervals.append(_attempt_interval(row, identifiers, sequences))
        grouped[job_id].append(row)

    for job_id, rows in grouped.items():
        rows.sort(key=lambda row: row["attempt_number"])
        numbers = [row["attempt_number"] for row in rows]
        if numbers != list(range(1, len(rows) + 1)) or jobs[job_id].get(
            "attempt"
        ) != len(rows):
            raise WorkflowFailure("public and persisted attempt generations disagree")
    recovery_rows = grouped[state["recovery_job_id"]]
    first_recovery = recovery_rows[0]
    if any(
        first_recovery[name] is None
        for name in (
            "create_intent_at",
            "unit_id",
            "proof_id",
            "proof_unit_id",
            "proof_recorded_at",
            "proof_acknowledged_at",
        )
    ):
        raise WorkflowFailure("interrupted recovery attempt lacks real-unit proof")
    intervals.sort()
    if any(previous[1] > current[0] for previous, current in pairwise(intervals)):
        raise WorkflowFailure("reverse attempt safety intervals overlap")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("prepare", "verify"):
        command = subparsers.add_parser(operation)
        command.add_argument("--base-url", required=True)
        command.add_argument(
            "--profile", choices=("standalone", "distributed"), required=True
        )
        command.add_argument("--scenario", choices=_SCENARIOS, required=True)
        command.add_argument("--state-file", type=Path, required=True)
        if operation == "verify":
            command.add_argument("--diagnostics-file", type=Path)
            command.add_argument("--result-receipt", type=Path)
    command = subparsers.add_parser("diagnostics")
    command.add_argument("--state-file", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--wait-for-recovery-attempt", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            prepare(args.base_url, args.profile, args.scenario, args.state_file)
        elif args.operation == "verify":
            verify(
                args.base_url,
                args.profile,
                args.scenario,
                args.state_file,
                args.diagnostics_file,
                args.result_receipt,
            )
        else:
            diagnostics(
                args.state_file,
                args.output,
                wait_for_recovery_attempt=args.wait_for_recovery_attempt,
            )
    except (OSError, subprocess.SubprocessError, WorkflowFailure) as error:
        print(f"T73 reverse lifecycle E2E failed: {error}", file=sys.stderr)
        return 1
    print(f"T73 reverse lifecycle {args.operation} passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
