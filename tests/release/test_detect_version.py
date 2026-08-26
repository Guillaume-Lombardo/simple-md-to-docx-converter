"""Tests for trusted automatic release-version detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release.detect_version import (
    ReleaseVersionError,
    VersionTransition,
    detect_repository_transition,
    detect_transition,
    main,
)

pytestmark = pytest.mark.unit

BEFORE = "1" * 40
HEAD = "2" * 40


def _project(version: str, *, extra: str = "", attempt: object | None = None) -> bytes:
    release = ""
    if attempt is not None:
        value = str(attempt).lower() if isinstance(attempt, bool) else repr(attempt)
        release = f"[tool.markweave.release]\nattempt = {value}\n"
    return (
        f'[project]\nname = "markweave"\nversion = "{version}"\n{extra}{release}'
    ).encode()


def test_detects_real_final_version_transition() -> None:
    assert detect_transition(_project("0.2.0"), _project("0.3.0")) == (
        VersionTransition("0.3.0", "v0.3.0")
    )


def test_pyproject_change_without_version_change_is_a_noop() -> None:
    assert (
        detect_transition(
            _project("0.3.0"),
            _project("0.3.0", extra='description = "changed"\n'),
        )
        is None
    )


def test_detects_exact_same_version_release_attempt_increment() -> None:
    assert detect_transition(
        _project("0.3.0"), _project("0.3.0", attempt=2)
    ) == VersionTransition("0.3.0", "v0.3.0")


@pytest.mark.parametrize(
    ("previous_attempt", "current_attempt", "message"),
    [(2, 1, "must not decrease"), (1, 3, "increase by exactly 1")],
)
def test_rejects_invalid_same_version_release_attempt_transition(
    previous_attempt: int, current_attempt: int, message: str
) -> None:
    with pytest.raises(ReleaseVersionError, match=message):
        detect_transition(
            _project("0.3.0", attempt=previous_attempt),
            _project("0.3.0", attempt=current_attempt),
        )


@pytest.mark.parametrize("attempt", [0, -1, True, "two"])
def test_rejects_invalid_release_attempt(attempt: object) -> None:
    with pytest.raises(ReleaseVersionError, match="positive integer"):
        detect_transition(_project("0.3.0"), _project("0.3.0", attempt=attempt))


def test_new_version_must_reset_release_attempt() -> None:
    with pytest.raises(ReleaseVersionError, match="reset the release attempt"):
        detect_transition(_project("0.3.0", attempt=2), _project("0.4.0", attempt=2))


def test_equivalent_canonical_spelling_transition_is_valid() -> None:
    assert detect_transition(_project("0.3"), _project("0.3.0")) == (
        VersionTransition("0.3.0", "v0.3.0")
    )


@pytest.mark.parametrize(
    ("previous", "current"),
    [("0.4.0", "0.3.0"), ("1.0", "0.99")],
)
def test_rejects_version_downgrade(previous: str, current: str) -> None:
    with pytest.raises(ReleaseVersionError, match="must not be lower"):
        detect_transition(_project(previous), _project(current))


@pytest.mark.parametrize(
    "version",
    ["invalid", "0.4.0rc1", "0.4.0.dev1", "0.4.0+local", "1!0.4.0", "00.4.0"],
)
def test_rejects_invalid_or_nonfinal_current_version(version: str) -> None:
    with pytest.raises(ReleaseVersionError):
        detect_transition(_project("0.3.0"), _project(version))


def test_repository_detection_binds_before_and_exact_head(
    tmp_path: Path, mocker
) -> None:
    (tmp_path / "pyproject.toml").write_bytes(_project("0.3.0"))
    inspected = mocker.patch(
        "scripts.release.detect_version._git_output",
        side_effect=[f"{HEAD}\n".encode(), _project("0.2.0")],
    )

    transition = detect_repository_transition(
        repository=tmp_path, before=BEFORE, head=HEAD
    )

    assert transition == VersionTransition("0.3.0", "v0.3.0")
    assert inspected.call_args_list == [
        mocker.call(("-C", str(tmp_path), "rev-parse", "HEAD")),
        mocker.call(("-C", str(tmp_path), "show", f"{BEFORE}:pyproject.toml")),
    ]


def test_repository_detection_rejects_mismatched_or_zero_sha(
    tmp_path: Path, mocker
) -> None:
    inspected = mocker.patch(
        "scripts.release.detect_version._git_output", return_value=b"3" * 40
    )
    with pytest.raises(ReleaseVersionError, match="checked-out HEAD"):
        detect_repository_transition(repository=tmp_path, before=BEFORE, head=HEAD)
    inspected.assert_called_once()

    with pytest.raises(ReleaseVersionError, match="before"):
        detect_repository_transition(repository=tmp_path, before="0" * 40, head=HEAD)


def test_cli_writes_changed_and_noop_outputs(tmp_path: Path, mocker) -> None:
    output = tmp_path / "github-output"
    detected = mocker.patch(
        "scripts.release.detect_version.detect_repository_transition",
        side_effect=[VersionTransition("0.3.0", "v0.3.0"), None],
    )

    arguments = [
        "--repository",
        str(tmp_path),
        "--before",
        BEFORE,
        "--head",
        HEAD,
        "--github-output",
        str(output),
    ]
    assert main(arguments) == 0
    assert main(arguments) == 0

    assert output.read_text(encoding="utf-8") == (
        f"changed=true\nversion=0.3.0\ntag=v0.3.0\nsource-sha={HEAD}\nchanged=false\n"
    )
    assert detected.call_count == 2
