"""Final-image workflow for the T34 template CLI family."""

from __future__ import annotations

import argparse
import json
import subprocess

from tests.e2e.cli_workflow import (
    _exec_prefix,
    _failure,
    _is_secret_free,
    _run_pty,
)

_TEMPLATE_FONTS = (
    "Aptos",
    "Aptos Display",
    "Calibri",
    "Cambria",
    "Cambria Math",
    "Consolas",
    "Courier New",
    "Times New Roman",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    namespace = parser.parse_args()
    result = _template_workflow(
        namespace.container,
        namespace.profile,
        _exec_prefix(namespace.container, tty=True),
        _exec_prefix(namespace.container, tty=False),
    )
    return result if result is not None else 0


def _template_workflow(  # noqa: PLR0911, PLR0912 - bounded E2E stages
    container: str,
    profile: str,
    pty_prefix: list[str],
    plain_prefix: list[str],
) -> int | None:
    """Exercise the installed template CLI against the final image's real backend."""
    admin_username = "e2e-admin"
    admin_password = "e2e-admin-password"  # noqa: S105 - isolated E2E fixture
    admin_profile = f"template-admin-{profile}"
    try:
        login, output = _run_pty(
            [
                *pty_prefix,
                "login",
                "--url",
                "http://127.0.0.1:8080",
                "--username",
                admin_username,
                "--profile",
                admin_profile,
            ],
            admin_password,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return _failure("template admin login", type(error).__name__)
    if login != 0 or not _is_secret_free(output, admin_password):
        return _failure(
            "template admin login",
            f"exit={login}; secret_free={_is_secret_free(output, admin_password)}",
        )

    fonts = [item for font in _TEMPLATE_FONTS for item in ("--font", font)]
    created = _json_command(
        plain_prefix,
        [
            "templates",
            "create",
            "--name",
            f"CLI {profile}",
            "--description",
            "Final-image template CLI workflow",
            "--file",
            "/evidence/browser-template.docx",
            *fonts,
            "--profile",
            admin_profile,
        ],
        "template create",
    )
    if isinstance(created, int):
        return created
    template_id = _required_json_string(created, "id")
    version_id = _required_json_string(created, "current_version_id")
    created_etag = _required_json_string(created, "etag")
    if template_id is None or version_id is None or created_etag is None:
        return _failure("template create", "missing identity or ETag")

    for stage, arguments in (
        (
            "template list",
            ["templates", "list", "--profile", admin_profile],
        ),
        (
            "template show",
            ["templates", "show", template_id, "--profile", admin_profile],
        ),
        (
            "template current download",
            [
                "templates",
                "download",
                template_id,
                "--output",
                "/work/t34-current.docx",
                "--profile",
                admin_profile,
            ],
        ),
    ):
        if (failure := _plain_command(plain_prefix, arguments, stage)) is not None:
            return failure

    updated = _json_command(
        plain_prefix,
        [
            "templates",
            "update",
            template_id,
            "--name",
            f"CLI updated {profile}",
            "--description",
            "Updated through the final-image CLI",
            "--etag",
            created_etag,
            "--profile",
            admin_profile,
        ],
        "template update",
    )
    if isinstance(updated, int):
        return updated
    updated_etag = _required_json_string(updated, "etag")
    if updated_etag is None:
        return _failure("template update", "missing ETag")
    stale = _run_captured(
        plain_prefix,
        [
            "templates",
            "update",
            template_id,
            "--name",
            "Lost update",
            "--description",
            "Must fail",
            "--etag",
            created_etag,
            "--profile",
            admin_profile,
        ],
    )
    if stale.returncode != 1 or "template_precondition_failed" not in stale.stderr:
        return _failure("stale template ETag", f"exit={stale.returncode}")

    replaced = _json_command(
        plain_prefix,
        [
            "templates",
            "replace",
            template_id,
            "--file",
            "/evidence/browser-template.docx",
            *fonts,
            "--etag",
            updated_etag,
            "--profile",
            admin_profile,
        ],
        "template replace",
    )
    if isinstance(replaced, int):
        return replaced
    replaced_etag = _required_json_string(replaced, "etag")
    if replaced_etag is None:
        return _failure("template replace", "missing ETag")
    versions = _json_command(
        plain_prefix,
        ["templates", "versions", template_id, "--profile", admin_profile],
        "template versions",
    )
    if isinstance(versions, int):
        return versions
    items = versions.get("items")
    if not isinstance(items, list) or len(items) < 2:
        return _failure("template versions", "immutable history is incomplete")
    if (
        failure := _plain_command(
            plain_prefix,
            [
                "templates",
                "version-download",
                template_id,
                version_id,
                "--output",
                "/work/t34-version.docx",
                "--profile",
                admin_profile,
            ],
            "template version download",
        )
    ) is not None:
        return failure
    restored = _json_command(
        plain_prefix,
        [
            "templates",
            "restore",
            template_id,
            version_id,
            "--etag",
            replaced_etag,
            "--profile",
            admin_profile,
        ],
        "template restore",
    )
    if isinstance(restored, int):
        return restored
    restored_etag = _required_json_string(restored, "etag")
    if restored_etag is None:
        return _failure("template restore", "missing ETag")

    for stage, arguments in (
        (
            "preferred template",
            [
                "templates",
                "preferred",
                "--template-id",
                template_id,
                "--profile",
                admin_profile,
            ],
        ),
        (
            "fallback template",
            ["templates", "fallback", template_id, "--profile", admin_profile],
        ),
    ):
        if (failure := _plain_command(plain_prefix, arguments, stage)) is not None:
            return failure
    archived = _json_command(
        plain_prefix,
        [
            "templates",
            "archive",
            template_id,
            "--etag",
            restored_etag,
            "--force",
            "--profile",
            admin_profile,
        ],
        "template archive",
    )
    if isinstance(archived, int):
        return archived
    archived_etag = _required_json_string(archived, "etag")
    if archived_etag is None:
        return _failure("template archive", "missing ETag")
    guarded = _run_captured(
        plain_prefix,
        [
            "templates",
            "delete",
            template_id,
            "--etag",
            archived_etag,
            "--force",
            "--profile",
            admin_profile,
        ],
    )
    if guarded.returncode != 1 or "template" not in guarded.stderr.casefold():
        return _failure("guarded template deletion", f"exit={guarded.returncode}")
    if (
        failure := _plain_command(
            plain_prefix,
            ["logout", "--profile", admin_profile],
            "template admin logout",
        )
    ) is not None:
        return failure
    return None


def _run_captured(
    prefix: list[str], arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*prefix, "--json", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )


def _plain_command(prefix: list[str], arguments: list[str], stage: str) -> int | None:
    result = _run_captured(prefix, arguments)
    if result.returncode != 0:
        return _failure(stage, f"exit={result.returncode}")
    return None


def _json_command(
    prefix: list[str], arguments: list[str], stage: str
) -> dict[str, object] | int:
    result = _run_captured(prefix, arguments)
    if result.returncode != 0:
        return _failure(stage, f"exit={result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError, UnicodeError:
        return _failure(stage, "invalid JSON output")
    if not isinstance(payload, dict):
        return _failure(stage, "JSON object output required")
    return payload


def _required_json_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


if __name__ == "__main__":
    raise SystemExit(main())
