"""Exercise conversion commands inside an exact rootless final image."""

from __future__ import annotations

import argparse
import errno
import json
import os
import pty
import select
import subprocess
import sys
import time
from uuid import UUID

_STATE_HOME = "/tmp/markweave-t33-cli-state"  # noqa: S108 - container tmpfs
_SOURCE = "/tmp/markweave-t33-source.md"  # noqa: S108 - container tmpfs
_REVERSE_SOURCE = "/tmp/markweave-t71-source.rtf"  # noqa: S108 - container tmpfs
_REVERSE_RESULT = "/tmp/markweave-t71-result.md"  # noqa: S108 - container tmpfs
_RESULT = "/tmp/markweave-t33-result.zip"  # noqa: S108 - container tmpfs
_MANIFEST = "/tmp/markweave-t33-manifest.json"  # noqa: S108 - container tmpfs
_LOCAL_IMAGES = "/tmp/markweave-t104"  # noqa: S108 - container tmpfs
_MISSING_IMAGE = "Markdown image is missing or unapproved."
_ESCAPING_IMAGE = "Markdown image path escapes the archive."
_LOCAL_IMAGE_SETUP = """
import io, zipfile
from pathlib import Path
from PIL import Image

root = Path({root!r})
project = root / "project"
(project / "assets").mkdir(parents=True)

def png(width, height):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "#336699").save(output, format="PNG")
    return output.getvalue()

(project / "assets" / "pixel.png").write_bytes(png(12, 8))
(project / "assets" / "unused.png").write_bytes(png(4, 4))
(project / "assets" / "wide.png").write_bytes(png(2001, 1))
(project / "assets" / "large.png").write_bytes(b"\\x89PNG" + bytes(1_000_001))
(root / "outside.png").write_bytes(png(4, 4))
texts = {{
    "report.md": "# Local images\\n\\n![Pixel](assets/pixel.png)\\n",
    "missing.md": "![Missing](assets/absent.png)\\n",
    "escape.md": "![Escape](../outside.png)\\n",
    "large.md": "![Large](assets/large.png)\\n",
    "wide.md": "![Wide](assets/wide.png)\\n",
}}
for name, text in texts.items():
    (project / name).write_text(text, encoding="utf-8")

def stored(name, entries):
    with zipfile.ZipFile(root / name, "w", zipfile.ZIP_STORED) as archive:
        for member, content in entries:
            archive.writestr(member, content)

stored("bypass-missing.zip", [("document.md", texts["missing.md"]),
                              ("assets/other.png", png(4, 4))])
stored("bypass-traversal.zip", [("document.md", "![T](../escape.png)"),
                                ("../escape.png", png(4, 4))])
"""


def main() -> int:  # noqa: PLR0911, PLR0912, PLR0915 - bounded E2E driver
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    parser.add_argument("--reverse-held-queue", action="store_true")
    parser.add_argument("--reverse-upload-max-bytes", type=int, default=1_000_000)
    arguments = parser.parse_args()
    prefix = _exec_prefix(arguments.container, tty=False)
    username = "e2e-admin"
    password = "e2e-admin-password"  # noqa: S105 - final-image fixture only
    setup = _inside(
        arguments.container,
        "from pathlib import Path; "
        f"Path({_SOURCE!r}).write_text('# CLI final image\\n', encoding='utf-8'); "
        f"Path({_REVERSE_SOURCE!r}).write_bytes(b'{{\\\\rtf1 Reverse CLI final image}}')",
    )
    if setup.returncode != 0:
        return _failure("source setup", setup.returncode)
    login, login_output = _run_pty(
        [
            *_exec_prefix(arguments.container, tty=True),
            "login",
            "--url",
            "http://127.0.0.1:8080",
            "--username",
            username,
        ],
        password,
    )
    if login != 0 or password in login_output:
        return _failure("login", login)
    if arguments.reverse_held_queue:
        failure = _exercise_reverse_held_queue(
            prefix, arguments.profile, arguments.reverse_upload_max_bytes
        )
        if failure is not None:
            return _failure(failure, 1)
        print(f"Reverse held-queue CLI E2E passed for {arguments.profile}.")
        return 0
    options_result = _json_result(_run([*prefix, "--json", "conversion-options"]))
    options = (
        options_result.get("conversion_options")
        if isinstance(options_result, dict)
        else None
    )
    selected = options.get("resolved_template") if isinstance(options, dict) else None
    if (
        not isinstance(options, dict)
        or options.get("conversion_upload_max_bytes") != 1_000_000
        or options.get("selection_source") != "system_fallback"
        or not isinstance(selected, dict)
        or selected.get("current_version_id") != options.get("template_version_id")
    ):
        return _failure("conversion options", 1)
    try:
        UUID(str(selected["id"]))
        UUID(str(selected["owner_id"]))
        UUID(str(options["template_version_id"]))
    except KeyError, ValueError:
        return _failure("conversion options identity", 1)
    human_options = _run([*prefix, "conversion-options"])
    if not _has_exact_stdout(
        human_options,
        "Upload limit: 1000000 bytes; template selection: system fallback.\n",
    ):
        return _failure("conversion options human output", human_options.returncode)
    key = f"t33-final-image-{arguments.profile}"
    submission = _run(
        [
            *prefix,
            "--json",
            "convert",
            _SOURCE,
            "--output",
            "both",
            "--idempotency-key",
            key,
        ]
    )
    job = _json_result(submission)
    if job is None or job.get("template_mode") != "pandoc-default":
        return _failure("submission", submission.returncode)
    try:
        job_id = str(UUID(str(job["id"])))
    except KeyError, ValueError:
        return _failure("submission identity", submission.returncode)
    replay = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "convert",
                _SOURCE,
                "--output",
                "both",
                "--idempotency-key",
                key,
            ]
        )
    )
    if replay is None or replay.get("id") != job_id:
        return _failure("idempotent replay", 1)
    listing = _json_result(_run([*prefix, "--json", "jobs", "list", "--limit", "10"]))
    items = listing.get("items") if listing is not None else None
    if not isinstance(items, list) or not any(
        item.get("id") == job_id for item in items if isinstance(item, dict)
    ):
        return _failure("job list", 1)
    shown = _json_result(_run([*prefix, "--json", "jobs", "show", job_id]))
    if shown is None or shown.get("id") != job_id:
        return _failure("job show", 1)
    waited = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "--timeout",
                "180",
                "jobs",
                "wait",
                job_id,
                "--poll-interval",
                "1",
            ],
            timeout=190,
        )
    )
    if waited is None or waited.get("state") != "succeeded":
        return _failure("job wait", 1)
    for command, destination in (("download", _RESULT), ("manifest", _MANIFEST)):
        result = _json_result(
            _run([*prefix, "--json", "jobs", command, job_id, destination])
        )
        if result is None or result.get("status") != "downloaded":
            return _failure(f"{command} result", 1)
    refused = _run([*prefix, "jobs", "download", job_id, _RESULT])
    if refused.returncode != 1 or "already exists" not in refused.stderr:
        return _failure("download clobber refusal", refused.returncode)
    overwritten = _run([*prefix, "jobs", "download", job_id, _RESULT, "--overwrite"])
    if overwritten.returncode != 0:
        return _failure("download overwrite", overwritten.returncode)
    validation = _inside(
        arguments.container,
        "import json; from pathlib import Path; "
        f"assert Path({_RESULT!r}).read_bytes().startswith(b'PK'); "
        f"assert isinstance(json.loads(Path({_MANIFEST!r}).read_text()), dict)",
    )
    if validation.returncode != 0:
        return _failure("download validation", validation.returncode)
    local_images = _exercise_local_images(prefix, arguments.container)
    if local_images is not None:
        return _failure(local_images, 1)
    logout = _run([*prefix, "logout"])
    if logout.returncode != 0:
        return _failure("logout", logout.returncode)
    cleanup = _inside(
        arguments.container,
        "import shutil; from pathlib import Path; "
        f"shutil.rmtree({_STATE_HOME!r}, ignore_errors=True); "
        f"shutil.rmtree({_LOCAL_IMAGES!r}, ignore_errors=True); "
        f"[Path(path).unlink(missing_ok=True) for path in "
        f"{[_SOURCE, _REVERSE_SOURCE, _REVERSE_RESULT, _RESULT, _MANIFEST]!r}]",
    )
    if cleanup.returncode != 0:
        return _failure("cleanup", cleanup.returncode)
    print(f"Conversion CLI final-image E2E passed for {arguments.profile}.")
    return 0


def _exercise_local_images(  # noqa: PLR0911 - bounded E2E assertions
    prefix: list[str], container: str
) -> str | None:
    """Verify T104 client-side packaging and unchanged service validation."""
    setup = _inside(container, _LOCAL_IMAGE_SETUP.format(root=_LOCAL_IMAGES))
    if setup.returncode != 0:
        return "local image setup"
    project = f"{_LOCAL_IMAGES}/project"
    packaged = _json_result(
        _run([*prefix, "--json", "convert", f"{project}/report.md"])
    )
    if (
        packaged is None
        or packaged.get("packaged_images") != 1
        or packaged.get("source_filename") != "source.zip"
    ):
        return "local image packaging"
    job_id = str(packaged.get("id"))
    if _wait(prefix, job_id).get("state") != "succeeded":
        return "local image conversion"
    result = f"{_LOCAL_IMAGES}/report.docx"
    downloaded = _json_result(
        _run([*prefix, "--json", "jobs", "download", job_id, result])
    )
    if downloaded is None or downloaded.get("status") != "downloaded":
        return "local image download"
    media = _inside(
        container,
        "import zipfile; "
        f"z = zipfile.ZipFile({result!r}); "
        "media = [n for n in z.namelist() if n.startswith('word/media/')]; "
        "assert len(media) == 1, media; "
        "assert z.read(media[0]).startswith(b'\\x89PNG')",
    )
    if media.returncode != 0:
        return "local image media"
    for name, code, message in (
        ("missing.md", "validation", _MISSING_IMAGE),
        ("escape.md", "validation", _ESCAPING_IMAGE),
        ("large.md", "conversion_request_invalid", None),
    ):
        error = _json_error(_run([*prefix, "--json", "convert", f"{project}/{name}"]))
        if (
            error is None
            or error.get("code") != code
            or (message is not None and error.get("message") != message)
        ):
            return f"local image refusal {name}"
    for source, message in (
        (f"{project}/wide.md", "Document image exceeds configured limits."),
        (f"{_LOCAL_IMAGES}/bypass-missing.zip", _MISSING_IMAGE),
        (f"{_LOCAL_IMAGES}/bypass-traversal.zip", "Document archive is invalid."),
    ):
        submitted = _json_result(_run([*prefix, "--json", "convert", source]))
        if submitted is None:
            return f"local image service submission {source}"
        failed = _wait(prefix, str(submitted.get("id")))
        if (
            failed.get("state") != "failed"
            or failed.get("error_code") != "validation"
            or failed.get("error_message") != message
        ):
            return f"local image service validation {source}"
    return None


def _wait(prefix: list[str], job_id: str) -> dict[str, object]:
    _run(
        [
            *prefix,
            "--timeout",
            "180",
            "jobs",
            "wait",
            job_id,
            "--poll-interval",
            "1",
        ],
        timeout=190,
    )
    shown = _json_result(_run([*prefix, "--json", "jobs", "show", job_id]))
    return shown if shown is not None else {}


def _json_error(result: subprocess.CompletedProcess[str]) -> dict[str, object] | None:
    if result.returncode != 1:
        return None
    try:
        error = json.loads(result.stderr).get("error")
    except AttributeError, ValueError:
        return None
    return error if isinstance(error, dict) else None


def _exercise_reverse_held_queue(  # noqa: PLR0911, PLR0912 - bounded E2E assertions
    prefix: list[str], profile: str, expected_upload_max_bytes: int
) -> str | None:
    """Verify queued reverse cancellation while the harness holds workers idle."""
    reverse_capabilities = _json_result(
        _run([*prefix, "--json", "jobs", "reverse", "capabilities"])
    )
    reverse_contract = (
        reverse_capabilities.get("reversion_capabilities")
        if isinstance(reverse_capabilities, dict)
        else None
    )
    if (
        not isinstance(reverse_contract, dict)
        or reverse_contract.get("maximum_upload_bytes") != expected_upload_max_bytes
        or reverse_contract.get("execution")
        != {"local": True, "ocr": False, "hosted_fallback": False}
    ):
        actual = (
            reverse_contract.get("maximum_upload_bytes")
            if isinstance(reverse_contract, dict)
            else "missing"
        )
        return f"reverse capabilities upload limit expected={expected_upload_max_bytes} actual={actual}"
    reverse_key = f"t71-final-image-{profile}"
    reverse_submission = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "jobs",
                "reverse",
                "submit",
                _REVERSE_SOURCE,
                "--idempotency-key",
                reverse_key,
            ]
        )
    )
    if reverse_submission is None or reverse_submission.get("state") != "queued":
        return "reverse submission"
    try:
        reverse_job_id = str(UUID(str(reverse_submission["id"])))
    except KeyError, ValueError:
        return "reverse submission identity"
    reverse_replay = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "jobs",
                "reverse",
                "submit",
                _REVERSE_SOURCE,
                "--idempotency-key",
                reverse_key,
            ]
        )
    )
    if reverse_replay is None or reverse_replay.get("id") != reverse_job_id:
        return "reverse idempotent replay"
    second_submission = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "jobs",
                "reverse",
                "submit",
                _REVERSE_SOURCE,
                "--idempotency-key",
                f"{reverse_key}-second",
            ]
        )
    )
    if second_submission is None or second_submission.get("state") != "queued":
        return "reverse second queued submission"
    try:
        second_job_id = str(UUID(str(second_submission["id"])))
    except KeyError, ValueError:
        return "reverse second submission identity"
    quota_json = _run(
        [
            *prefix,
            "--json",
            "jobs",
            "reverse",
            "submit",
            _REVERSE_SOURCE,
            "--idempotency-key",
            f"{reverse_key}-quota",
        ]
    )
    try:
        quota_error = json.loads(quota_json.stderr).get("error")
    except AttributeError, ValueError:
        quota_error = None
    if (
        quota_json.returncode != 1
        or not isinstance(quota_error, dict)
        or quota_error.get("code") != "reversion_user_quota_exceeded"
    ):
        return "reverse active quota JSON error"
    quota_human = _run(
        [
            *prefix,
            "jobs",
            "reverse",
            "submit",
            _REVERSE_SOURCE,
            "--idempotency-key",
            f"{reverse_key}-quota-human",
        ]
    )
    if quota_human.returncode != 1 or "quota is exhausted" not in quota_human.stderr:
        return "reverse active quota human error"
    reverse_listing = _json_result(
        _run([*prefix, "--json", "jobs", "reverse", "list", "--limit", "10"])
    )
    reverse_items = (
        reverse_listing.get("items") if isinstance(reverse_listing, dict) else None
    )
    if not isinstance(reverse_items, list) or not any(
        item.get("id") == reverse_job_id
        for item in reverse_items
        if isinstance(item, dict)
    ):
        return "reverse job list"
    reverse_shown = _json_result(
        _run([*prefix, "--json", "jobs", "reverse", "show", reverse_job_id])
    )
    if reverse_shown is None or reverse_shown.get("id") != reverse_job_id:
        return "reverse job show"
    reverse_wait = _run(
        [
            *prefix,
            "--timeout",
            "1",
            "jobs",
            "reverse",
            "wait",
            reverse_job_id,
            "--poll-interval",
            "0.1",
        ]
    )
    if reverse_wait.returncode != 1 or "did not finish" not in reverse_wait.stderr:
        return "reverse bounded wait"
    reverse_cancelled = _json_result(
        _run([*prefix, "--json", "jobs", "reverse", "cancel", reverse_job_id])
    )
    if reverse_cancelled is None or reverse_cancelled.get("state") != "cancelled":
        return "reverse job cancel"
    second_cancelled = _json_result(
        _run([*prefix, "--json", "jobs", "reverse", "cancel", second_job_id])
    )
    if second_cancelled is None or second_cancelled.get("state") != "cancelled":
        return "reverse second job cancel"
    reverse_download = _run(
        [*prefix, "jobs", "reverse", "download", reverse_job_id, _REVERSE_RESULT]
    )
    if reverse_download.returncode != 1 or "conflicts" not in reverse_download.stderr:
        return "reverse unavailable download"
    return None


def _run(
    arguments: list[str], *, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, check=False, capture_output=True, text=True, timeout=timeout
    )


def _inside(container: str, code: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "podman",
            "exec",
            container,
            "/opt/md-converter/venv/bin/python",
            "-c",
            code,
        ]
    )


def _json_result(result: subprocess.CompletedProcess[str]) -> dict[str, object] | None:
    if result.returncode != 0 or result.stderr:
        return None
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _has_exact_stdout(result: subprocess.CompletedProcess[str], expected: str) -> bool:
    return result.returncode == 0 and not result.stderr and result.stdout == expected


def _run_pty(arguments: list[str], password: str) -> tuple[int, str]:
    master, slave = pty.openpty()
    output = bytearray()
    process: subprocess.Popen[bytes] | None = None
    sent = False
    try:
        process = subprocess.Popen(
            arguments, stdin=slave, stdout=slave, stderr=slave, close_fds=True
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            if (
                _read_pty(master, output, timeout=0.1)
                and not sent
                and b"Password: " in output
            ):
                os.write(master, password.encode() + b"\n")
                sent = True
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
            return 1, "deadline"
        while _read_pty(master, output, timeout=0):
            pass
        return process.returncode, output.decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if slave >= 0:
            os.close(slave)
        os.close(master)


def _read_pty(master: int, output: bytearray, *, timeout: float) -> bool:
    readable, _, _ = select.select([master], [], [], timeout)
    if not readable:
        return False
    try:
        chunk = os.read(master, 4096)
    except OSError as error:
        if error.errno == errno.EIO:
            return False
        raise
    if not chunk:
        return False
    output.extend(chunk)
    return True


def _exec_prefix(container: str, *, tty: bool) -> list[str]:
    prefix = [
        "podman",
        "exec",
        "--env",
        f"XDG_STATE_HOME={_STATE_HOME}",
        container,
        "/usr/bin/env",
        "-i",
        f"XDG_STATE_HOME={_STATE_HOME}",
        "/opt/md-converter/venv/bin/markweave",
    ]
    if tty:
        prefix[2:2] = ["--interactive", "--tty"]
    return prefix


def _failure(stage: str, exit_code: int) -> int:
    print(f"Conversion CLI E2E failed at {stage}: exit={exit_code}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
