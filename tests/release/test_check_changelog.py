"""Tests for fail-closed material-version changelog validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release.check_changelog import (
    ChangelogError,
    check_changelog,
    check_repository_transition,
    main,
)

pytestmark = pytest.mark.unit

BEFORE = "1" * 40
HEAD = "2" * 40


def _project(version: str) -> bytes:
    return f'[project]\nname = "markweave"\nversion = "{version}"\n'.encode()


def _changelog(version: str) -> str:
    return f"## [{version}] - 2026-08-30\n\n### Added\n\n- A release entry.\n"


def test_requires_a_dated_entry_for_a_changed_version() -> None:
    with pytest.raises(ChangelogError, match="lacks a dated entry"):
        check_changelog(_project("0.4.0"), _project("0.5.0"), "# Changelog\n")


def test_accepts_an_exact_dated_entry_for_a_changed_version() -> None:
    check_changelog(_project("0.4.0"), _project("0.5.0"), _changelog("0.5.0"))


def test_ignores_non_material_pyproject_changes() -> None:
    check_changelog(_project("0.4.0"), _project("0.4.0"), "# Changelog\n")


@pytest.mark.parametrize(
    ("changelog", "message"),
    [
        ("## [0.5.0]\n", "lacks a dated entry"),
        ("## [0.5.0] - 2026-8-30\n", "lacks a dated entry"),
        ("## [0.5.0] - 2026-08-30 extra\n", "lacks a dated entry"),
    ],
)
def test_rejects_ambiguous_or_malformed_entries(changelog: str, message: str) -> None:
    with pytest.raises(ChangelogError, match=message):
        check_changelog(_project("0.4.0"), _project("0.5.0"), changelog)


def test_rejects_invalid_or_downgraded_versions() -> None:
    with pytest.raises(ChangelogError, match="canonical final version"):
        check_changelog(_project("0.4.0"), _project("0.5.0rc1"), "")
    with pytest.raises(ChangelogError, match="must not be lower"):
        check_changelog(_project("0.5.0"), _project("0.4.0"), "")


@pytest.mark.parametrize("version", ["invalid", "0.5.0rc1", "0.5.0+local", "1!0.5.0"])
def test_rejects_nonfinal_or_noncanonical_versions(version: str) -> None:
    with pytest.raises(ChangelogError):
        check_changelog(_project("0.4.0"), _project(version), "")


def test_repository_check_binds_the_exact_before_and_head(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "pyproject.toml").write_bytes(_project("0.5.0"))
    (tmp_path / "CHANGELOG.md").write_text(_changelog("0.5.0"), encoding="utf-8")
    inspected = mocker.patch(
        "scripts.release.check_changelog._git_output",
        side_effect=[f"{HEAD}\n".encode(), _project("0.4.0")],
    )

    check_repository_transition(repository=tmp_path, before=BEFORE, head=HEAD)

    assert inspected.call_args_list == [
        mocker.call(("-C", str(tmp_path), "rev-parse", "HEAD")),
        mocker.call(("-C", str(tmp_path), "show", f"{BEFORE}:pyproject.toml")),
    ]


def test_repository_check_rejects_an_untrusted_head(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    inspected = mocker.patch(
        "scripts.release.check_changelog._git_output", return_value=b"3" * 40
    )

    with pytest.raises(ChangelogError, match="checked-out HEAD"):
        check_repository_transition(repository=tmp_path, before=BEFORE, head=HEAD)

    inspected.assert_called_once()


@pytest.mark.parametrize("before", ["0" * 40, "short", "A" * 40])
def test_repository_check_rejects_invalid_before_sha(
    tmp_path: Path, before: str
) -> None:
    with pytest.raises(ChangelogError, match="before must be a nonzero full Git SHA"):
        check_repository_transition(repository=tmp_path, before=before, head=HEAD)


def test_repository_check_reports_missing_current_files(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "scripts.release.check_changelog._git_output",
        side_effect=[f"{HEAD}\n".encode(), _project("0.4.0")],
    )
    (tmp_path / "pyproject.toml").write_bytes(_project("0.5.0"))

    with pytest.raises(
        ChangelogError, match="cannot read the current changelog transition"
    ):
        check_repository_transition(repository=tmp_path, before=BEFORE, head=HEAD)


def test_command_line_reports_a_safe_error(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocked = mocker.patch(
        "scripts.release.check_changelog.check_repository_transition",
        side_effect=ChangelogError("missing entry"),
    )

    with pytest.raises(SystemExit) as exited:
        main(["--repository", str(tmp_path), "--before", BEFORE, "--head", HEAD])

    assert exited.value.code == 1
    assert capsys.readouterr().err == "error: missing entry\n"
    mocked.assert_called_once_with(repository=tmp_path, before=BEFORE, head=HEAD)


def test_command_line_returns_zero_for_a_valid_check(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocked = mocker.patch("scripts.release.check_changelog.check_repository_transition")

    assert (
        main(["--repository", str(tmp_path), "--before", BEFORE, "--head", HEAD]) == 0
    )

    mocked.assert_called_once_with(repository=tmp_path, before=BEFORE, head=HEAD)
