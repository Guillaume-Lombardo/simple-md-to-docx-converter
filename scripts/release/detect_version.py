"""Detect a canonical final-version transition at an exact Git commit."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Sequence

FULL_SHA = re.compile(r"[0-9a-f]{40}")
ZERO_SHA = "0" * 40


class ReleaseVersionError(ValueError):
    """The source does not describe a safe automatic release transition."""


@dataclass(frozen=True)
class VersionTransition:
    """A changed public version and its exact Git tag."""

    version: str
    tag: str


def _public_final_version(document: bytes, *, label: str) -> str:
    try:
        project = tomllib.loads(document.decode())["project"]
        raw = project["version"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise ReleaseVersionError(f"{label} has no valid project.version") from error
    if not isinstance(raw, str) or raw != raw.strip():
        raise ReleaseVersionError(f"{label} project.version must be a string")
    try:
        parsed = Version(raw)
    except InvalidVersion as error:
        raise ReleaseVersionError(
            f"{label} project.version is not valid PEP 440"
        ) from error
    if raw != str(parsed):
        raise ReleaseVersionError(
            f"{label} project.version must use canonical PEP 440 spelling"
        )
    if (
        parsed.is_prerelease
        or parsed.is_devrelease
        or parsed.local is not None
        or parsed.epoch != 0
    ):
        raise ReleaseVersionError(
            f"{label} project.version must be a final public version"
        )
    return raw


def detect_transition(previous: bytes, current: bytes) -> VersionTransition | None:
    """Return a release transition only when the authoritative version changed."""
    previous_version = _public_final_version(previous, label="previous pyproject.toml")
    current_version = _public_final_version(current, label="current pyproject.toml")
    if Version(current_version) < Version(previous_version):
        raise ReleaseVersionError(
            "current project.version must not be lower than the previous version"
        )
    if current_version == previous_version:
        return None
    return VersionTransition(current_version, f"v{current_version}")


def _git_output(arguments: Sequence[str]) -> bytes:
    try:
        return subprocess.run(  # noqa: S603 - fixed Git executable and bounded arguments
            ["/usr/bin/git", *arguments],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseVersionError(
            "cannot inspect the exact Git version transition"
        ) from error


def _validated_sha(value: str, *, label: str) -> str:
    if FULL_SHA.fullmatch(value) is None or value == ZERO_SHA:
        raise ReleaseVersionError(f"{label} must be a nonzero full Git SHA")
    return value


def detect_repository_transition(
    *, repository: Path, before: str, head: str
) -> VersionTransition | None:
    """Detect a transition from the push's exact before/head commit pair."""
    before_sha = _validated_sha(before, label="before")
    head_sha = _validated_sha(head, label="head")
    actual_head = _git_output(("-C", str(repository), "rev-parse", "HEAD"))
    if actual_head.decode().strip() != head_sha:
        raise ReleaseVersionError(
            "checked-out HEAD does not match the trusted push SHA"
        )
    previous = _git_output(
        ("-C", str(repository), "show", f"{before_sha}:pyproject.toml")
    )
    current = (repository / "pyproject.toml").read_bytes()
    return detect_transition(previous, current)


def _write_outputs(path: Path, transition: VersionTransition | None, head: str) -> None:
    lines = [f"changed={'true' if transition else 'false'}"]
    if transition is not None:
        lines.extend(
            (
                f"version={transition.version}",
                f"tag={transition.tag}",
                f"source-sha={head}",
            )
        )
    with path.open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Write trusted release outputs for a GitHub push event."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--before", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--github-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        transition = detect_repository_transition(
            repository=args.repository,
            before=args.before,
            head=args.head,
        )
        _write_outputs(args.github_output, transition, args.head)
    except (OSError, ReleaseVersionError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - workflow entry point
    raise SystemExit(main())
