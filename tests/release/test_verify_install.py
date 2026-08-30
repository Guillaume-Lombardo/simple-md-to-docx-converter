"""Tests for clean installation of a manifest-bound wheel copy."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import markweave
from scripts.release.artifacts import ArtifactError, ArtifactSet
from scripts.release.verify_install import (
    CONSOLE_TIMEOUT_SECONDS,
    ENVIRONMENT_TIMEOUT_SECONDS,
    IMPORT_TIMEOUT_SECONDS,
    INSTALL_TIMEOUT_SECONDS,
    PUBLIC_IMPORT_CHECK,
    verify_clean_install,
)

pytestmark = pytest.mark.unit


def test_public_import_check_rejects_legacy_import_after_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The isolated verification script checks both the new and removed imports."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(sys, "argv", ["check", "markweave", "0.4.0"])

    with pytest.raises(
        SystemExit, match="legacy md_converter import remains installed"
    ):
        exec(PUBLIC_IMPORT_CHECK, {})  # noqa: S102 - isolated verifier contract


def test_public_import_check_rejects_application_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distribution and public application versions must identify one release."""
    monkeypatch.setattr(markweave, "__version__", "9.9.9")
    monkeypatch.setattr(sys, "argv", ["check", "markweave", "0.4.0"])

    with pytest.raises(SystemExit, match=r"unexpected markweave\.__version__"):
        exec(PUBLIC_IMPORT_CHECK, {})  # noqa: S102 - isolated verifier contract


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactSet:
    """Provide artifact paths with their canonical manifest-bound digest."""
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = directory / "markweave-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"verified wheel")
    sdist = directory / "markweave-0.1.0.tar.gz"
    sdist.write_bytes(b"verified sdist")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return ArtifactSet(
        wheel=wheel,
        sdist=sdist,
        integrity=((wheel.name, digest), (sdist.name, "b" * 64)),
    )


def test_clean_install_uses_private_digest_bound_copy_and_cleans_up(
    artifacts: ArtifactSet, mocker: MockerFixture
) -> None:
    """Only a private copy matching the manifest reaches isolated Python paths."""
    events: list[str] = []

    def verified(*args: object, **kwargs: object) -> ArtifactSet:
        events.append("verified")
        return artifacts

    calls: list[tuple[tuple[str, ...], Path, int]] = []

    def executed(
        command: tuple[str, ...], *, cwd: Path, label: str, timeout: int
    ) -> None:
        events.append(label)
        calls.append((command, cwd, timeout))
        if len(calls) == 2:
            private_wheel = Path(command[-1])
            assert private_wheel != artifacts.wheel
            assert private_wheel.read_bytes() == artifacts.wheel.read_bytes()
            assert private_wheel.parent.name == "artifacts"

    verify = mocker.patch(
        "scripts.release.verify_install.verify_release", side_effect=verified
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    mocker.patch("scripts.release.verify_install.run_command", side_effect=executed)

    result = verify_clean_install(
        artifacts.wheel.parent,
        expected_name="markweave",
        expected_version="0.1.0",
    )

    verify.assert_called_once_with(
        artifacts.wheel.parent,
        expected_name="markweave",
        expected_version="0.1.0",
        manifest_name="release-integrity.json",
    )
    assert events == [
        "verified",
        "clean environment creation",
        "exact wheel installation",
        "isolated public import check",
        "isolated console version check",
        "isolated console help check",
    ]
    venv_command, root, timeout = calls[0]
    environment = root / "venv"
    python = environment / "bin" / "python"
    assert timeout == ENVIRONMENT_TIMEOUT_SECONDS
    assert venv_command == (
        "/usr/bin/uv",
        "venv",
        "--python",
        "3.14",
        "--no-project",
        str(environment),
    )
    private_wheel = root / "artifacts" / artifacts.wheel.name
    assert calls[1] == (
        (
            "/usr/bin/uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--strict",
            str(private_wheel),
        ),
        root,
        INSTALL_TIMEOUT_SECONDS,
    )
    assert calls[2] == (
        (
            str(python),
            "-I",
            "-c",
            PUBLIC_IMPORT_CHECK,
            "markweave",
            "0.1.0",
        ),
        root,
        IMPORT_TIMEOUT_SECONDS,
    )
    console = environment / "bin" / "markweave"
    assert calls[3] == (
        (str(python), "-I", str(console), "--version"),
        root,
        CONSOLE_TIMEOUT_SECONDS,
    )
    assert calls[4] == (
        (str(python), "-I", str(console), "--help"),
        root,
        CONSOLE_TIMEOUT_SECONDS,
    )
    assert all(cwd == root for _, cwd, _ in calls)
    assert root != Path.cwd()
    assert not root.exists()
    assert result.wheel_name == artifacts.wheel.name
    assert result.sha256 == artifacts.sha256_for(artifacts.wheel)


def test_integrity_failure_prevents_environment_creation(
    artifacts: ArtifactSet, mocker: MockerFixture
) -> None:
    """No environment or subprocess is created for unverified artifacts."""
    mocker.patch(
        "scripts.release.verify_install.verify_release",
        side_effect=ArtifactError("integrity failed"),
    )
    temporary = mocker.patch(
        "scripts.release.verify_install.tempfile.TemporaryDirectory"
    )
    run = mocker.patch("scripts.release.verify_install.run_command")

    with pytest.raises(ArtifactError, match="integrity failed"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="markweave",
            expected_version="0.1.0",
        )

    temporary.assert_not_called()
    run.assert_not_called()


def test_wheel_change_after_verification_fails_before_uv(
    artifacts: ArtifactSet, mocker: MockerFixture
) -> None:
    """A TOCTOU replacement cannot reach the clean installer."""

    def replace_after_verification(*args: object, **kwargs: object) -> ArtifactSet:
        artifacts.wheel.write_bytes(b"attacker replacement")
        return artifacts

    mocker.patch(
        "scripts.release.verify_install.verify_release",
        side_effect=replace_after_verification,
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    run = mocker.patch("scripts.release.verify_install.run_command")

    with pytest.raises(ArtifactError, match="changed before private copy"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="markweave",
            expected_version="0.1.0",
        )

    run.assert_not_called()


@pytest.mark.parametrize("failing_call", [1, 2, 3, 4, 5])
def test_subprocess_failure_stops_later_steps_and_cleans_up(
    artifacts: ArtifactSet, mocker: MockerFixture, failing_call: int
) -> None:
    """Each external failure aborts later work and removes private artifacts."""
    mocker.patch(
        "scripts.release.verify_install.verify_release", return_value=artifacts
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    calls: list[Path] = []

    def executed(
        command: tuple[str, ...], *, cwd: Path, label: str, timeout: int
    ) -> None:
        calls.append(cwd)
        if len(calls) == failing_call:
            raise ArtifactError(f"{label} failed")

    mocker.patch("scripts.release.verify_install.run_command", side_effect=executed)

    with pytest.raises(ArtifactError, match="failed"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="markweave",
            expected_version="0.1.0",
        )

    assert len(calls) == failing_call
    assert len(set(calls)) == 1
    assert not calls[0].exists()


def test_blocked_subprocess_times_out_and_cleans_up(
    artifacts: ArtifactSet, mocker: MockerFixture
) -> None:
    """A blocking external command has a hard deadline and private cleanup."""
    mocker.patch(
        "scripts.release.verify_install.verify_release", return_value=artifacts
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    roots: list[Path] = []

    def blocked(
        command: tuple[str, ...], *, cwd: Path, label: str, timeout: int
    ) -> None:
        roots.append(cwd)
        raise ArtifactError(f"{label} timed out")

    mocker.patch("scripts.release.verify_install.run_command", side_effect=blocked)
    with pytest.raises(ArtifactError, match="timed out"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="markweave",
            expected_version="0.1.0",
        )
    assert roots and not roots[0].exists()
