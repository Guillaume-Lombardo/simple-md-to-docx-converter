"""Fail closed when a final project-version transition lacks a changelog entry."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_it import MarkdownIt
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Sequence


FULL_SHA = re.compile(r"[0-9a-f]{40}")
ZERO_SHA = "0" * 40
ENTRY = re.compile(r"^\[(?P<version>[^]]+)\] - (?P<date>\d{4}-\d{2}-\d{2})$")
MARKDOWN = MarkdownIt()


class ChangelogError(ValueError):
    """The source does not provide a safe material-version changelog entry."""


def _version(document: bytes, *, label: str) -> str:
    try:
        raw = tomllib.loads(document.decode())["project"]["version"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise ChangelogError(f"{label} has no valid project.version") from error
    if not isinstance(raw, str) or raw != raw.strip():
        raise ChangelogError(f"{label} project.version must be a string")
    try:
        parsed = Version(raw)
    except InvalidVersion as error:
        raise ChangelogError(f"{label} project.version is not valid PEP 440") from error
    if raw != str(parsed) or parsed.is_prerelease or parsed.is_devrelease:
        raise ChangelogError(
            f"{label} project.version must be a canonical final version"
        )
    if parsed.local is not None or parsed.epoch != 0:
        raise ChangelogError(
            f"{label} project.version must be a canonical final version"
        )
    return raw


def _has_release_heading(changelog: str, *, version: str) -> bool:
    """Return whether a real level-two Markdown heading names the release."""
    tokens = MARKDOWN.parse(changelog)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        inline = tokens[index + 1]
        if inline.type != "inline":
            continue
        matched = ENTRY.fullmatch(inline.content)
        if matched is None or matched["version"] != version:
            continue
        try:
            date.fromisoformat(matched["date"])
        except ValueError:
            continue
        return True
    return False


def check_changelog(previous: bytes, current: bytes, changelog: str) -> None:
    """Require a dated entry when the public final version changes."""
    previous_version = _version(previous, label="previous pyproject.toml")
    current_version = _version(current, label="current pyproject.toml")
    if Version(current_version) < Version(previous_version):
        raise ChangelogError("current project.version must not be lower than previous")
    if current_version == previous_version:
        return
    if not _has_release_heading(changelog, version=current_version):
        raise ChangelogError(
            f"CHANGELOG.md lacks a dated entry for version {current_version}"
        )


def _git_output(arguments: Sequence[str]) -> bytes:
    try:
        return subprocess.run(  # noqa: S603 - fixed Git executable and arguments
            ["/usr/bin/git", *arguments], check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ChangelogError(
            "cannot inspect the exact Git version transition"
        ) from error


def _sha(value: str, *, label: str) -> str:
    if FULL_SHA.fullmatch(value) is None or value == ZERO_SHA:
        raise ChangelogError(f"{label} must be a nonzero full Git SHA")
    return value


def check_repository_transition(*, repository: Path, before: str, head: str) -> None:
    """Check the trusted before/head pair and the checked-out changelog."""
    before_sha = _sha(before, label="before")
    head_sha = _sha(head, label="head")
    actual_head = _git_output(("-C", str(repository), "rev-parse", "HEAD"))
    if actual_head.decode().strip() != head_sha:
        raise ChangelogError("checked-out HEAD does not match the trusted push SHA")
    previous = _git_output(
        ("-C", str(repository), "show", f"{before_sha}:pyproject.toml")
    )
    try:
        current = (repository / "pyproject.toml").read_bytes()
        changelog = (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError as error:
        raise ChangelogError("cannot read the current changelog transition") from error
    check_changelog(previous, current, changelog)


def main(argv: Sequence[str] | None = None) -> int:
    """Check a material version transition from an exact trusted Git push."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--before", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        check_repository_transition(
            repository=args.repository, before=args.before, head=args.head
        )
    except (OSError, ChangelogError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - workflow entry point
    raise SystemExit(main())
