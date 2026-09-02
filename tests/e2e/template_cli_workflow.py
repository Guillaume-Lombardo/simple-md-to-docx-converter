"""Final-image workflow for the T34 template CLI family."""

from __future__ import annotations

import argparse
import json
import subprocess

from tests.e2e.cli_workflow import (
    _assert_secret_free,
    _blocked_process_snapshot,
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
_SETUP_USERS_SCRIPT = """
import json
import sys
from urllib.request import Request, urlopen

from markweave.cli.profiles import ProfileStore

configuration = json.load(sys.stdin)
profile = ProfileStore().load(configuration["profile"])
for user in configuration["users"]:
    request = Request(
        profile.service_url + "/api/v1/admin/users",
        data=json.dumps(user).encode(),
        headers={
            "Content-Type": "application/json",
            "Cookie": profile.session_state or "",
            "X-CSRF-Token": profile.csrf_state or "",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 201:
            raise SystemExit(1)
""".strip()


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


def _template_workflow(  # noqa: PLR0911, PLR0912, PLR0915 - E2E matrix
    container: str,
    profile: str,
    pty_prefix: list[str],
    plain_prefix: list[str],
) -> int | None:
    """Exercise the installed template CLI against the final image's real backend."""
    admin_profile = f"template-admin-{profile}"
    owner_profile = f"template-owner-{profile}"
    other_profile = f"template-other-{profile}"
    admin_password = "e2e-admin-password"  # noqa: S105 - isolated E2E fixture
    owner_username = f"t34-owner-{profile}"
    owner_password = f"T34Owner-{profile}-Fixture9!"
    other_username = f"t34-other-{profile}"
    other_password = f"T34Other-{profile}-Fixture9!"

    failure = _login(
        container,
        pty_prefix,
        username="e2e-admin",
        password=admin_password,
        profile=admin_profile,
        inspect_process=False,
        stage="template admin login",
    )
    if failure is not None:
        return failure
    if (
        failure := _setup_users(
            container,
            admin_profile,
            ((owner_username, owner_password), (other_username, other_password)),
        )
    ) is not None:
        return failure
    for username, password, account_profile, stage in (
        (owner_username, owner_password, owner_profile, "template owner login"),
        (other_username, other_password, other_profile, "template other login"),
    ):
        failure = _login(
            container,
            pty_prefix,
            username=username,
            password=password,
            profile=account_profile,
            inspect_process=True,
            stage=stage,
        )
        if failure is not None:
            return failure

    initial_context = _json_command(
        plain_prefix,
        ["templates", "context", "--profile", owner_profile],
        "initial template context",
    )
    if isinstance(initial_context, int):
        return initial_context
    initial = initial_context.get("template_context")
    if (
        not isinstance(initial, dict)
        or initial.get("preferred_template_id") is not None
        or not isinstance(initial.get("system_fallback_template_id"), str)
        or initial.get("template_max_archive_bytes") != 1_000_000
    ):
        return _failure("initial template context", "unexpected metadata")
    failure = _plain_command(
        plain_prefix,
        ["templates", "context", "--profile", owner_profile],
        "template context human output",
        expected_stdout="Template upload limit: 1000000 bytes.\n",
    )
    if failure is not None:
        return failure

    fonts = [item for font in _TEMPLATE_FONTS for item in ("--font", font)]
    created = _json_command(
        plain_prefix,
        [
            "templates",
            "create",
            "--name",
            f"CLI owner {profile}",
            "--description",
            "Final-image template CLI workflow",
            "--file",
            "/evidence/browser-template.docx",
            *fonts,
            "--profile",
            owner_profile,
        ],
        "owner template create",
    )
    if isinstance(created, int):
        return created
    template_id = _required_json_string(created, "id")
    version_id = _required_json_string(created, "current_version_id")
    owner_id = _required_json_string(created, "owner_id")
    created_etag = _required_json_string(created, "etag")
    if None in (template_id, version_id, owner_id, created_etag):
        return _failure("owner template create", "missing identity, owner, or ETag")
    assert template_id is not None
    assert version_id is not None
    assert owner_id is not None
    assert created_etag is not None

    searched = _json_command(
        plain_prefix,
        [
            "templates",
            "search",
            "--name",
            f"CLI owner {profile}",
            "--profile",
            other_profile,
        ],
        "non-owner template search",
    )
    if isinstance(searched, int):
        return searched
    items = searched.get("items")
    if not isinstance(items, list) or not any(
        isinstance(item, dict) and item.get("id") == template_id for item in items
    ):
        return _failure("non-owner template search", "created template is absent")

    for stage, arguments in (
        (
            "non-owner template list",
            ["templates", "list", "--profile", other_profile],
        ),
        (
            "non-owner template show",
            ["templates", "show", template_id, "--profile", other_profile],
        ),
        (
            "non-owner template download",
            [
                "templates",
                "download",
                template_id,
                "--output",
                "/work/t34-current.docx",
                "--profile",
                other_profile,
            ],
        ),
    ):
        if (failure := _plain_command(plain_prefix, arguments, stage)) is not None:
            return failure

    forbidden = _run_captured(
        plain_prefix,
        [
            "templates",
            "update",
            template_id,
            "--name",
            "Forbidden update",
            "--description",
            "A second user must not mutate another user's template",
            "--etag",
            created_etag,
            "--profile",
            other_profile,
        ],
    )
    if forbidden.returncode != 1 or "forbidden" not in forbidden.stderr.casefold():
        return _failure(
            "non-owner template update denial", f"exit={forbidden.returncode}"
        )
    fallback_denied = _run_captured(
        plain_prefix,
        ["templates", "fallback", template_id, "--profile", other_profile],
    )
    if (
        fallback_denied.returncode != 1
        or "forbidden" not in fallback_denied.stderr.casefold()
    ):
        return _failure(
            "non-admin fallback denial", f"exit={fallback_denied.returncode}"
        )

    updated = _json_command(
        plain_prefix,
        [
            "templates",
            "update",
            template_id,
            "--name",
            f"CLI admin-updated {profile}",
            "--description",
            "Administrator intervention through the final-image CLI",
            "--etag",
            created_etag,
            "--profile",
            admin_profile,
        ],
        "administrator intervention",
    )
    if isinstance(updated, int):
        return updated
    updated_etag = _required_json_string(updated, "etag")
    if updated_etag is None or updated.get("owner_id") != owner_id:
        return _failure("administrator intervention", "owner changed or ETag missing")

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
            owner_profile,
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
            owner_profile,
        ],
        "owner template replace",
    )
    if isinstance(replaced, int):
        return replaced
    replaced_etag = _required_json_string(replaced, "etag")
    if replaced_etag is None:
        return _failure("owner template replace", "missing ETag")
    versions = _json_command(
        plain_prefix,
        ["templates", "versions", template_id, "--profile", other_profile],
        "template versions",
    )
    if isinstance(versions, int):
        return versions
    version_items = versions.get("items")
    if not isinstance(version_items, list) or len(version_items) < 2:
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
                other_profile,
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
            owner_profile,
        ],
        "owner template restore",
    )
    if isinstance(restored, int):
        return restored
    restored_etag = _required_json_string(restored, "etag")
    if restored_etag is None:
        return _failure("owner template restore", "missing ETag")

    if (
        failure := _plain_command(
            plain_prefix,
            [
                "templates",
                "preferred",
                "--template-id",
                template_id,
                "--profile",
                owner_profile,
            ],
            "owner preferred template",
        )
    ) is not None:
        return failure
    fallback_template = _json_command(
        plain_prefix,
        [
            "templates",
            "create",
            "--name",
            f"CLI fallback {profile}",
            "--description",
            "Final-image fallback command fixture",
            "--file",
            "/evidence/browser-template.docx",
            *fonts,
            "--profile",
            admin_profile,
        ],
        "fallback template create",
    )
    if isinstance(fallback_template, int):
        return fallback_template
    fallback_id = _required_json_string(fallback_template, "id")
    if fallback_id is None:
        return _failure("fallback template create", "missing identity")
    if (
        failure := _plain_command(
            plain_prefix,
            ["templates", "fallback", fallback_id, "--profile", admin_profile],
            "administrator fallback template",
        )
    ) is not None:
        return failure

    selected_context = _json_command(
        plain_prefix,
        ["templates", "context", "--profile", owner_profile],
        "selected template context",
    )
    if isinstance(selected_context, int):
        return selected_context
    selected = selected_context.get("template_context")
    if (
        not isinstance(selected, dict)
        or selected.get("preferred_template_id") != template_id
        or selected.get("system_fallback_template_id") != fallback_id
        or selected.get("template_max_archive_bytes") != 1_000_000
    ):
        return _failure("selected template context", "unexpected metadata")

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
        "administrator template archive",
    )
    if isinstance(archived, int):
        return archived
    archived_etag = _required_json_string(archived, "etag")
    if archived_etag is None:
        return _failure("administrator template archive", "missing ETag")

    for stage, account_profile in (
        ("owner archived visibility", owner_profile),
        ("administrator archived visibility", admin_profile),
    ):
        visible = _json_command(
            plain_prefix,
            ["templates", "show", template_id, "--profile", account_profile],
            stage,
        )
        if isinstance(visible, int):
            return visible
        if visible.get("status") != "archived":
            return _failure(stage, "template is not archived")
    hidden = _run_captured(
        plain_prefix,
        ["templates", "show", template_id, "--profile", other_profile],
    )
    if hidden.returncode != 1 or "template_not_found" not in hidden.stderr:
        return _failure("non-owner archived visibility", f"exit={hidden.returncode}")

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
    if guarded.returncode != 1 or "template_precondition_failed" not in guarded.stderr:
        return _failure("guarded template deletion", f"exit={guarded.returncode}")
    if (
        failure := _plain_command(
            plain_prefix,
            ["templates", "preferred", "--clear", "--profile", owner_profile],
            "clear owner preferred template",
        )
    ) is not None:
        return failure
    deleted = _json_command(
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
        "successful guarded template deletion",
    )
    if isinstance(deleted, int):
        return deleted
    if deleted.get("id") != template_id or deleted.get("status") != "deleted":
        return _failure("successful guarded template deletion", "invalid result")

    for account_profile, stage in (
        (other_profile, "template other logout"),
        (owner_profile, "template owner logout"),
        (admin_profile, "template admin logout"),
    ):
        if (
            failure := _plain_command(
                plain_prefix, ["logout", "--profile", account_profile], stage
            )
        ) is not None:
            return failure
    return None


def _login(  # noqa: PLR0913 - explicit credential-boundary contract
    container: str,
    prefix: list[str],
    *,
    username: str,
    password: str,
    profile: str,
    inspect_process: bool,
    stage: str,
) -> int | None:
    callback = None
    if inspect_process:
        callback = lambda: _assert_secret_free(  # noqa: E731 - delayed PTY hook
            _blocked_process_snapshot(container), password
        )
    try:
        result, output = _run_pty(
            [
                *prefix,
                "login",
                "--url",
                "http://127.0.0.1:8080",
                "--username",
                username,
                "--profile",
                profile,
            ],
            password,
            on_prompt=callback,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return _failure(stage, type(error).__name__)
    if result != 0 or not _is_secret_free(output, password):
        return _failure(
            stage,
            f"exit={result}; secret_free={_is_secret_free(output, password)}",
        )
    return None


def _setup_users(
    container: str,
    admin_profile: str,
    users: tuple[tuple[str, str], ...],
) -> int | None:
    payload = {
        "profile": admin_profile,
        "users": [
            {"username": username, "password": password} for username, password in users
        ],
    }
    try:
        result = subprocess.run(
            _user_setup_command(container),
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _failure("template regular-user setup", type(error).__name__)
    if result.returncode != 0:
        return _failure("template regular-user setup", f"exit={result.returncode}")
    return None


def _user_setup_command(container: str) -> list[str]:
    """Keep fixture credentials off process arguments and environment variables."""
    return [
        "podman",
        "exec",
        "--interactive",
        "--env",
        "XDG_STATE_HOME=/tmp/markweave-cli-state",
        container,
        "/usr/bin/env",
        "-i",
        "XDG_STATE_HOME=/tmp/markweave-cli-state",
        "/opt/md-converter/venv/bin/python",
        "-c",
        _SETUP_USERS_SCRIPT,
    ]


def _run_captured(
    prefix: list[str], arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    command = [*prefix, "--json", *arguments]
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "")
    except OSError:
        return subprocess.CompletedProcess(command, 126, "", "")


def _plain_command(
    prefix: list[str],
    arguments: list[str],
    stage: str,
    *,
    expected_stdout: str | None = None,
) -> int | None:
    result = _run_captured(prefix, arguments)
    if result.returncode != 0:
        return _failure(stage, f"exit={result.returncode}")
    if expected_stdout is not None and result.stdout != expected_stdout:
        return _failure(stage, "unexpected output")
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
