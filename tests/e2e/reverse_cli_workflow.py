"""Exercise installed reverse-job CLI workflows against final backend images.

The T73 orchestrator supplies a running final backend and its matched reverse-attempt
image/broker. This driver deliberately owns no container lifecycle or fault injection.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import select
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from markweave.reversions.manifest import (
    ManifestResult,
    ManifestSource,
    canonical_manifest_bytes,
)
from tests.e2e import structured_pptx_workflow
from tests.e2e.administration_cli_workflow import _interactive, _login, _plain
from tests.e2e.cli_workflow import _exec_prefix

_ADMIN_PROFILE = "t73-admin"
_ALICE_PROFILE = "t73-alice"
_BOB_PROFILE = "t73-bob"
_UNAVAILABLE_PROFILE = "t73-unavailable"
_ALICE = "t73-reverse-alice"
_BOB = "t73-reverse-bob"
_ALICE_PASSWORD = "T73-reverse-alice-password"  # noqa: S105 - E2E fixture
_BOB_PASSWORD = "T73-reverse-bob-password"  # noqa: S105 - E2E fixture
_SOURCE = "/tmp/markweave-t73-reverse.docx"  # noqa: S108 - container tmpfs
_SCANNED_SOURCE = "/tmp/markweave-t73-scanned.pdf"  # noqa: S108 - container tmpfs
_UNSUPPORTED_SOURCE = "/tmp/markweave-t73-unsupported.txt"  # noqa: S108 - container tmpfs
_SCANNER_SOURCE = "/tmp/markweave-t73-eicar.docx"  # noqa: S108 - container tmpfs
_RESULT = "/tmp/markweave-t73-reverse-result"  # noqa: S108 - container tmpfs
_STRUCTURED_SOURCE = "/tmp/markweave-t83-edited.pptx"  # noqa: S108 - container tmpfs
_STRUCTURED_RESULT = "/tmp/markweave-t83-structured-result"  # noqa: S108 - container tmpfs
_DENIED_RESULT = "/tmp/markweave-t73-reverse-denied"  # noqa: S108 - container tmpfs
_CORPUS_SOURCE = Path("spikes/anydoc/corpus/docx/text.docx")
_SCANNED_CORPUS_SOURCE = Path("spikes/anydoc/corpus/pdf/handmade-scanned.pdf")
_STRUCTURED_CORPUS_SOURCE = Path("spikes/anydoc/corpus/pptx/pres.pptx")
_PROFILE_STATE_HOME = "/tmp/markweave-cli-state"  # noqa: S108 - container tmpfs
_UNAVAILABLE_HOLDER_PID = "/tmp/markweave-t73-unavailable-holder.pid"  # noqa: S108
_EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class WorkflowFailure(RuntimeError):
    """Carry a bounded stage name without printing document content."""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    parser.add_argument(
        "--phase",
        choices=("primary", "structured-pptx", "expiry", "unavailable"),
        default="primary",
    )
    arguments = parser.parse_args()
    try:
        if arguments.phase == "primary":
            _exercise_primary_success(arguments.container, arguments.profile)
        elif arguments.phase == "structured-pptx":
            _exercise_structured_pptx(arguments.container, arguments.profile)
        elif arguments.phase == "expiry":
            _exercise_expiry(arguments.container, arguments.profile)
        else:
            _exercise_unavailable(arguments.container, arguments.profile)
    except (OSError, subprocess.TimeoutExpired, WorkflowFailure) as error:
        print(f"T73 reverse CLI E2E failed: {error}", file=sys.stderr)
        return 1
    print(
        f"T73 reverse CLI E2E {arguments.phase} workflow passed for {arguments.profile}."
    )
    return 0


def _exercise_primary_success(container: str, profile: str) -> None:
    """Verify a real owner-scoped reverse lifecycle and its downloaded result."""
    if not _CORPUS_SOURCE.is_file():
        raise WorkflowFailure("redistributable DOCX corpus fixture is unavailable")
    _copy_source(container)
    _copy_fixture(container, _SCANNED_CORPUS_SOURCE, _SCANNED_SOURCE)
    _copy_bytes(container, _UNSUPPORTED_SOURCE, b"unsupported reverse source\n")
    _copy_bytes(container, _SCANNER_SOURCE, _EICAR)
    _create_users(container)
    _login(container, _ALICE_PROFILE, _ALICE, _ALICE_PASSWORD)
    plain = _exec_prefix(container, tty=False)
    _require_profile_identity(plain, _ALICE_PROFILE, _ALICE, "user")

    capabilities = _json(
        plain,
        ("--json", "jobs", "reverse", "capabilities", "--profile", _ALICE_PROFILE),
    )
    _require_capabilities(capabilities)
    human_capabilities = _command(
        plain, ("jobs", "reverse", "capabilities", "--profile", _ALICE_PROFILE)
    )
    _require_human(human_capabilities, "Reverse conversion supports")
    _assert_admission_rejections(plain, profile)
    _assert_scanner_rejection(plain, profile)
    before = _owner_job_ids(plain)

    key = f"t73-{profile}-reverse-success"
    submitted = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "submit",
            _SOURCE,
            "--idempotency-key",
            key,
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    job_id = _job_id(submitted, "submission")
    replay = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "submit",
            _SOURCE,
            "--idempotency-key",
            key,
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    if _job_id(replay, "idempotent replay") != job_id:
        raise WorkflowFailure("reverse idempotent replay changed job identity")
    _require_human(
        _command(
            plain,
            ("jobs", "reverse", "show", job_id, "--profile", _ALICE_PROFILE),
        ),
        "Reverse job",
    )
    listed = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "list",
            "--limit",
            "10",
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    items = listed.get("items")
    if not isinstance(items, list):
        raise WorkflowFailure("owner listing omitted submitted job")
    new_ids = {
        item.get("id")
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("id") not in before
    }
    if new_ids != {job_id}:
        raise WorkflowFailure("reverse idempotent replay created another owner job")
    _require_human(
        _command(
            plain,
            ("jobs", "reverse", "list", "--limit", "10", "--profile", _ALICE_PROFILE),
        ),
        "Listed",
    )
    waited = _json(
        plain,
        (
            "--json",
            "--timeout",
            "180",
            "jobs",
            "reverse",
            "wait",
            job_id,
            "--poll-interval",
            "1",
            "--profile",
            _ALICE_PROFILE,
        ),
        timeout=190,
    )
    if waited.get("state") != "succeeded":
        raise WorkflowFailure("successful reverse job did not reach succeeded")
    _require_human(
        _command(
            plain,
            (
                "--timeout",
                "30",
                "jobs",
                "reverse",
                "wait",
                job_id,
                "--poll-interval",
                "1",
                "--profile",
                _ALICE_PROFILE,
            ),
            timeout=40,
        ),
        "Reverse job",
    )
    downloaded = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "download",
            job_id,
            _RESULT,
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    if downloaded.get("status") != "downloaded":
        raise WorkflowFailure("result download did not report downloaded")
    result_digest = _inspect_download(container)
    _require_human(
        _command(
            plain,
            (
                "jobs",
                "reverse",
                "download",
                job_id,
                _RESULT,
                "--overwrite",
                "--profile",
                _ALICE_PROFILE,
            ),
        ),
        "Downloaded the reverse-job result.",
    )
    _assert_non_enumeration(container, job_id, result_digest)
    _cleanup(container)


def _exercise_structured_pptx(container: str, profile: str) -> None:
    """Exercise capability-default slide extraction through the installed CLI."""
    _copy_structured_source(container)
    _create_users(container)
    _login(container, _ALICE_PROFILE, _ALICE, _ALICE_PASSWORD)
    plain = _exec_prefix(container, tty=False)
    before = _owner_job_ids(plain)
    invalid = (
        "jobs",
        "reverse",
        "submit",
        _STRUCTURED_SOURCE,
        "--extraction",
        "anydoc",
        "--no-include-notes",
        "--idempotency-key",
        f"t83-{profile}-structured-invalid",
        "--profile",
        _ALICE_PROFILE,
    )
    _require_human_error(
        _command(plain, invalid, expected=1), "requires notes and images"
    )
    _require_error_code(
        _error_json(plain, ("--json", *invalid)), "extraction_options_invalid"
    )
    if _owner_job_ids(plain) != before:
        raise WorkflowFailure("invalid structured CLI options created a reverse job")
    submitted = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "submit",
            _STRUCTURED_SOURCE,
            "--extraction",
            "slides",
            "--idempotency-key",
            f"t83-{profile}-structured-slides",
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    job_id = _job_id(submitted, "structured slides submission")
    if submitted.get("options") != {
        "extraction": "slides",
        "include_notes": True,
        "include_images": True,
    }:
        raise WorkflowFailure("structured CLI capability defaults differ")
    completed = _json(
        plain,
        (
            "--json",
            "--timeout",
            "60",
            "jobs",
            "reverse",
            "wait",
            job_id,
            "--poll-interval",
            "1",
            "--profile",
            _ALICE_PROFILE,
        ),
        timeout=70,
    )
    if completed.get("state") != "succeeded":
        raise WorkflowFailure("structured CLI slides job did not succeed")
    downloaded = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "download",
            job_id,
            _STRUCTURED_RESULT,
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    if downloaded.get("status") != "downloaded":
        raise WorkflowFailure(
            "structured CLI result download did not report downloaded"
        )
    _inspect_structured_download(container)
    _cleanup_structured_result(container)


def _exercise_expiry(container: str, profile: str) -> None:
    """Require a retained reverse result to become unavailable within its live TTL."""
    _copy_source(container)
    _create_users(container)
    _login(container, _ALICE_PROFILE, _ALICE, _ALICE_PASSWORD)
    plain = _exec_prefix(container, tty=False)
    submitted = _json(
        plain,
        (
            "--json",
            "jobs",
            "reverse",
            "submit",
            _SOURCE,
            "--idempotency-key",
            f"t73-{profile}-reverse-expiry",
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    job_id = _job_id(submitted, "expiry submission")
    completed = _json(
        plain,
        (
            "--json",
            "--timeout",
            "30",
            "jobs",
            "reverse",
            "wait",
            job_id,
            "--poll-interval",
            "1",
            "--profile",
            _ALICE_PROFILE,
        ),
        timeout=40,
    )
    if completed.get("state") != "succeeded":
        raise WorkflowFailure("expiry source job did not succeed")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        shown = _json(
            plain,
            ("--json", "jobs", "reverse", "show", job_id, "--profile", _ALICE_PROFILE),
        )
        if shown.get("state") == "expired":
            break
        time.sleep(1)
    else:
        raise WorkflowFailure("reverse result did not expire within retention bound")
    destination = f"{_DENIED_RESULT}-expiry"
    download = (
        "jobs",
        "reverse",
        "download",
        job_id,
        destination,
        "--profile",
        _ALICE_PROFILE,
    )
    _require_human_error(_command(plain, download, expected=1), "error:")
    _require_error_code(_error_json(plain, ("--json", *download)), "reversion_conflict")
    if _container_path_exists(container, destination):
        raise WorkflowFailure("expired result download created a destination")
    _cleanup(container)


def _exercise_unavailable(container: str, profile: str) -> None:
    """Require a valid named profile to report a closed loopback service safely."""
    _create_users(container)
    _login(container, _ALICE_PROFILE, _ALICE, _ALICE_PASSWORD)
    holder = subprocess.Popen(
        [
            "podman",
            "exec",
            container,
            "/opt/md-converter/venv/bin/python",
            "-c",
            "import os,socket,time; s=socket.socket(); s.bind(('127.0.0.1', 0)); "
            f"open({_UNAVAILABLE_HOLDER_PID!r}, 'w').write(str(os.getpid())); "
            "print(s.getsockname()[1], flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        if (
            holder.stdout is None
            or not select.select([holder.stdout], [], [], 5)[0]
            or not (line := holder.stdout.readline()).strip().isdigit()
        ):
            raise WorkflowFailure("reserving closed loopback endpoint")
        port = int(line)
        _copy_unavailable_profile(container, port)
        _require_unavailable_holder(container, holder)
        plain = _exec_prefix(container, tty=False)
        command = ("jobs", "reverse", "capabilities", "--profile", _UNAVAILABLE_PROFILE)
        _require_human_error(
            _command(plain, command, expected=1), "could not be reached"
        )
        _require_error_code(_error_json(plain, ("--json", *command)), "network_error")
        _require_unavailable_holder(container, holder)
    finally:
        subprocess.run(
            [
                "podman",
                "exec",
                container,
                "/bin/sh",
                "-c",
                f'test ! -f {_UNAVAILABLE_HOLDER_PID} || kill "$(cat {_UNAVAILABLE_HOLDER_PID})"; rm -f {_UNAVAILABLE_HOLDER_PID}',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=5)
    _cleanup(container)


def _copy_unavailable_profile(container: str, port: int) -> None:
    code = (
        "import json; from pathlib import Path; "
        f"directory=Path({_PROFILE_STATE_HOME!r})/'markweave'/'profiles'; "
        f"value=json.loads((directory/{(_ALICE_PROFILE + '.json')!r}).read_text()); "
        f"value['name']={_UNAVAILABLE_PROFILE!r}; value['service_url']={'http://127.0.0.1:'!r}+str({port}); "
        f"target=directory/{(_UNAVAILABLE_PROFILE + '.json')!r}; target.write_text(json.dumps(value,sort_keys=True,separators=(',',':'))); target.chmod(0o600)"
    )
    result = subprocess.run(
        ["podman", "exec", container, "/opt/md-converter/venv/bin/python", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorkflowFailure("creating unavailable named profile")


def _owner_job_ids(prefix: Sequence[str]) -> set[str]:
    listed = _json(
        prefix,
        (
            "--json",
            "jobs",
            "reverse",
            "list",
            "--limit",
            "100",
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    items = listed.get("items")
    if not isinstance(items, list):
        raise WorkflowFailure("owner reverse job listing before idempotent replay")
    return {
        item.get("id")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _require_unavailable_holder(container: str, holder: subprocess.Popen[str]) -> None:
    if holder.poll() is not None or not _container_path_exists(
        container, _UNAVAILABLE_HOLDER_PID
    ):
        raise WorkflowFailure("closed loopback holder ended early")


def _copy_structured_source(container: str) -> None:
    """Create the shared edited PPTX source for the CLI and browser phases."""
    if not _STRUCTURED_CORPUS_SOURCE.is_file():
        raise WorkflowFailure(
            "redistributable PowerPoint corpus fixture is unavailable"
        )
    try:
        source = structured_pptx_workflow.edited_presentation(
            _STRUCTURED_CORPUS_SOURCE.read_bytes()
        )
    except structured_pptx_workflow.WorkflowFailure as error:
        raise WorkflowFailure("creating edited PowerPoint source") from error
    _copy_bytes(container, _STRUCTURED_SOURCE, source)


def _copy_source(container: str) -> None:
    _copy_bytes(container, _SOURCE, _normalized_fixture())


def _copy_fixture(container: str, source: Path, destination: str) -> None:
    if not source.is_file():
        raise WorkflowFailure("redistributable reverse corpus fixture is unavailable")
    _copy_bytes(container, destination, source.read_bytes())


def _copy_bytes(container: str, destination: str, source: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="markweave-t73-source-") as directory:
        fixture = Path(directory) / Path(destination).name
        fixture.write_bytes(source)
        result = subprocess.run(
            ["podman", "cp", str(fixture), f"{container}:{destination}"],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise WorkflowFailure("copying the redistributable corpus fixture")


def _normalized_fixture() -> bytes:
    """Apply the approved safe-link normalization without changing the corpus."""
    rebuilt = io.BytesIO()
    with (
        zipfile.ZipFile(_CORPUS_SOURCE) as source,
        zipfile.ZipFile(rebuilt, "w", compression=zipfile.ZIP_STORED) as destination,
    ):
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "word/_rels/document.xml.rels":
                content = content.replace(
                    b'Target="../../fixture-src/sibling.odt"',
                    b'Target="https://example.test/sibling"',
                )
            destination.writestr(info, content)
    return rebuilt.getvalue()


def _create_users(container: str) -> None:
    _login(container, _ADMIN_PROFILE, "e2e-admin", "e2e-admin-password")
    listed = _json(
        _exec_prefix(container, tty=False),
        ("--json", "users", "list", "--profile", _ADMIN_PROFILE),
    )
    users = listed.get("users")
    existing = (
        {
            item.get("username")
            for item in users
            if isinstance(item, dict) and isinstance(item.get("username"), str)
        }
        if isinstance(users, list)
        else None
    )
    if existing is None:
        raise WorkflowFailure("admin user listing for phase identities")
    for username, password in ((_ALICE, _ALICE_PASSWORD), (_BOB, _BOB_PASSWORD)):
        if username in existing:
            continue
        _interactive(
            container,
            (
                "users",
                "create",
                "--username",
                username,
                "--force",
                "--profile",
                _ADMIN_PROFILE,
            ),
            ((b"New password: ", password), (b"Confirm new password: ", password)),
        )


def _assert_non_enumeration(container: str, job_id: str, result_digest: str) -> None:
    """Require a second user and an administrator to receive the owner-safe error."""
    for profile, username, password, role in (
        (_BOB_PROFILE, _BOB, _BOB_PASSWORD, "user"),
        (_ADMIN_PROFILE, "e2e-admin", "e2e-admin-password", "admin"),
    ):
        _login(container, profile, username, password)
        plain = _exec_prefix(container, tty=False)
        _require_profile_identity(plain, profile, username, role)
        denied_destination = f"{_DENIED_RESULT}-{profile}"
        for command in (
            ("jobs", "reverse", "show", job_id, "--profile", profile),
            ("jobs", "reverse", "cancel", job_id, "--profile", profile),
            (
                "jobs",
                "reverse",
                "download",
                job_id,
                denied_destination,
                "--profile",
                profile,
            ),
        ):
            result = _command(plain, command, expected=1)
            if "not found" not in result.stderr.casefold():
                raise WorkflowFailure("cross-owner reverse job enumeration")
        if _container_path_exists(container, denied_destination):
            raise WorkflowFailure(
                "denied reverse result download created a destination"
            )
    _require_profile_files(container)
    _login(container, _ALICE_PROFILE, _ALICE, _ALICE_PASSWORD)
    owner = _exec_prefix(container, tty=False)
    retained = _json(
        owner,
        ("--json", "jobs", "reverse", "show", job_id, "--profile", _ALICE_PROFILE),
    )
    if retained.get("state") != "succeeded":
        raise WorkflowFailure("owner reverse job changed after denied cancellation")
    restored = _json(
        owner,
        (
            "--json",
            "jobs",
            "reverse",
            "download",
            job_id,
            _RESULT,
            "--overwrite",
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    if restored.get("status") != "downloaded":
        raise WorkflowFailure("owner result changed after denied cancellation")
    if _inspect_download(container) != result_digest:
        raise WorkflowFailure("owner result changed after denied cancellation")


def _assert_admission_rejections(prefix: Sequence[str], profile: str) -> None:
    """Exercise local unsupported input and final-image no-OCR rejection."""
    unsupported = (
        "jobs",
        "reverse",
        "submit",
        _UNSUPPORTED_SOURCE,
        "--profile",
        _ALICE_PROFILE,
    )
    _require_human_error(_command(prefix, unsupported, expected=1), "not supported")
    _require_error_code(
        _error_json(prefix, ("--json", *unsupported)), "source_type_invalid"
    )

    submitted = _json(
        prefix,
        (
            "--json",
            "jobs",
            "reverse",
            "submit",
            _SCANNED_SOURCE,
            "--idempotency-key",
            f"t73-{profile}-reverse-needs-ocr",
            "--profile",
            _ALICE_PROFILE,
        ),
    )
    job_id = _job_id(submitted, "scanned source submission")
    wait = (
        "--timeout",
        "30",
        "jobs",
        "reverse",
        "wait",
        job_id,
        "--poll-interval",
        "1",
        "--profile",
        _ALICE_PROFILE,
    )
    _require_human_error(_command(prefix, wait, expected=1, timeout=40), "error:")
    _require_error_code(_error_json(prefix, ("--json", *wait), timeout=40), "needs_ocr")


def _assert_scanner_rejection(prefix: Sequence[str], profile: str) -> None:
    """Require the opt-in EICAR scanner response before reverse admission."""
    command = (
        "jobs",
        "reverse",
        "submit",
        _SCANNER_SOURCE,
        "--idempotency-key",
        f"t73-{profile}-reverse-scanner",
        "--profile",
        _ALICE_PROFILE,
    )
    _require_human_error(
        _command(prefix, command, expected=1), "rejected by malware scanning"
    )
    _require_error_code(
        _error_json(prefix, ("--json", *command)), "upload_malware_detected"
    )


def _json(
    prefix: Sequence[str], command: Sequence[str], *, timeout: float = 30
) -> dict[str, object]:
    result = _command(prefix, command, timeout=timeout)
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise WorkflowFailure("JSON command output") from error
    if not isinstance(value, dict):
        raise WorkflowFailure("JSON command envelope")
    return value


def _error_json(
    prefix: Sequence[str], command: Sequence[str], *, timeout: float = 30
) -> dict[str, object]:
    result = _command(prefix, command, expected=1, timeout=timeout)
    try:
        value = json.loads(result.stderr)
    except ValueError as error:
        raise WorkflowFailure("JSON error output") from error
    if not isinstance(value, dict):
        raise WorkflowFailure("JSON error envelope")
    return value


def _require_error_code(value: dict[str, object], expected: str) -> None:
    error = value.get("error")
    actual = error.get("code") if isinstance(error, dict) else None
    if actual != expected:
        detail = actual if isinstance(actual, str) else "missing"
        raise WorkflowFailure(f"reverse CLI error code {expected}, received {detail}")


def _command(
    prefix: Sequence[str],
    command: Sequence[str],
    *,
    expected: int = 0,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return _plain(prefix, command, expected=expected, timeout=timeout)
    except RuntimeError as error:
        raise WorkflowFailure(" ".join(command[:4])) from error


def _require_capabilities(value: dict[str, object]) -> None:
    contract = value.get("reversion_capabilities")
    if not isinstance(contract, dict):
        raise WorkflowFailure("reverse capabilities JSON envelope")
    if contract.get("schema_version") != 1 or contract.get("execution") != {
        "local": True,
        "ocr": False,
        "hosted_fallback": False,
    }:
        raise WorkflowFailure("reverse capabilities contract")


def _job_id(value: dict[str, object], stage: str) -> str:
    try:
        return str(UUID(str(value["id"])))
    except (KeyError, ValueError) as error:
        raise WorkflowFailure(f"reverse {stage} job identity") from error


def _require_human(result: subprocess.CompletedProcess[str], expected: str) -> None:
    if result.stderr or expected not in result.stdout:
        raise WorkflowFailure(f"human command output: {expected}")


def _require_human_error(
    result: subprocess.CompletedProcess[str], expected: str
) -> None:
    if result.stdout or expected.casefold() not in result.stderr.casefold():
        raise WorkflowFailure(f"human error output: {expected}")


def _inspect_download(container: str) -> str:
    """Require the known DOCX fixture's deterministic Markdown-with-assets ZIP."""
    with tempfile.TemporaryDirectory(prefix="markweave-t73-result-") as directory:
        result = Path(directory) / "result.zip"
        copied = subprocess.run(
            ["podman", "cp", f"{container}:{_RESULT}", str(result)],
            check=False,
            capture_output=True,
            text=True,
        )
        if copied.returncode != 0:
            raise WorkflowFailure("copying downloaded reverse result for inspection")
        _inspect_fixture_package(result)
        return hashlib.sha256(result.read_bytes()).hexdigest()


def _inspect_structured_download(container: str) -> None:
    """Validate the installed CLI's selected slide-oriented package."""
    with tempfile.TemporaryDirectory(prefix="markweave-t83-result-") as directory:
        result = Path(directory) / "result.zip"
        copied = subprocess.run(
            ["podman", "cp", f"{container}:{_STRUCTURED_RESULT}", str(result)],
            check=False,
            capture_output=True,
            text=True,
        )
        if copied.returncode != 0:
            raise WorkflowFailure("copying structured CLI result for inspection")
        try:
            structured_pptx_workflow.validate_structured_package(
                result.read_bytes(), "slides"
            )
        except structured_pptx_workflow.WorkflowFailure as error:
            raise WorkflowFailure("structured CLI package differs") from error


def _inspect_fixture_package(path: Path) -> None:
    """Validate the corpus DOCX's expected asset reference and manifest evidence."""
    expected_asset = "assets/image-0001.png"
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != ["document.md", expected_asset, "manifest.json"]:
                raise WorkflowFailure("unexpected reverse ZIP layout")
            markdown = archive.read("document.md")
            asset = archive.read(expected_asset)
            manifest = archive.read("manifest.json")
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise WorkflowFailure(
            "reverse result is not the expected ZIP package"
        ) from error
    if (
        b"Inline image: ![tiny red dot](assets/image-0001.png) done." not in markdown
        or not asset.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise WorkflowFailure("reverse ZIP fixture Markdown or PNG asset")
    expected_manifest = canonical_manifest_bytes(
        ManifestSource("word", "docx"),
        ManifestResult("markdown_with_assets", 1, len(asset), 0),
    )
    if manifest != expected_manifest:
        raise WorkflowFailure("reverse ZIP traceability manifest")


def _require_profile_identity(
    prefix: Sequence[str], profile: str, username: str, role: str
) -> None:
    value = _json(prefix, ("--json", "whoami", "--profile", profile))
    if (
        value.get("profile") != profile
        or value.get("username") != username
        or value.get("role") != role
        or value.get("password_change_required") is not False
    ):
        raise WorkflowFailure("CLI profile isolation")


def _container_path_exists(container: str, path: str) -> bool:
    result = subprocess.run(
        ["podman", "exec", container, "/bin/sh", "-c", f"test -e {path}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _require_profile_files(container: str) -> None:
    """Prove explicitly named sessions stay separate from the default profile."""
    code = (
        "import stat; from pathlib import Path; "
        f"directory = Path({_PROFILE_STATE_HOME!r}) / 'markweave' / 'profiles'; "
        f"names = {{{_ADMIN_PROFILE!r}, {_ALICE_PROFILE!r}, {_BOB_PROFILE!r}}}; "
        "paths = [directory / f'{name}.json' for name in names]; "
        "assert all(path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths); "
        "assert len({path.resolve() for path in paths}) == len(paths); "
        "assert not (directory / 'default.json').exists()"
    )
    result = subprocess.run(
        [
            "podman",
            "exec",
            container,
            "/opt/md-converter/venv/bin/python",
            "-c",
            code,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorkflowFailure("CLI profile files are not isolated")


def _cleanup_structured_result(container: str) -> None:
    """Retain the shared PPTX source for the browser structured phase."""
    subprocess.run(
        ["podman", "exec", container, "/bin/sh", "-c", f"rm -f {_STRUCTURED_RESULT}"],
        check=False,
        capture_output=True,
        text=True,
    )


def _cleanup(container: str) -> None:
    subprocess.run(
        [
            "podman",
            "exec",
            container,
            "/bin/sh",
            "-c",
            f"rm -f {_SOURCE} {_SCANNED_SOURCE} {_UNSUPPORTED_SOURCE} {_SCANNER_SOURCE} {_RESULT} {_DENIED_RESULT}-*",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
