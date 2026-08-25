"""Tests for clean installation of a manifest-bound wheel copy."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release.artifacts import ArtifactError, ArtifactSet
from scripts.release.verify_install import (
    ENVIRONMENT_TIMEOUT_SECONDS,
    IMPORT_TIMEOUT_SECONDS,
    INSTALL_TIMEOUT_SECONDS,
    PUBLIC_IMPORT_CHECK,
    verify_clean_install,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactSet:
    """Provide artifact paths with their canonical manifest-bound digest."""
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = directory / "md_converter-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"verified wheel")
    sdist = directory / "md_converter-0.1.0.tar.gz"
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
    """Only a private copy matching the manifest reaches uv and isolated Python."""
    events: list[str] = []

    def verified(*args: object, **kwargs: object) -> ArtifactSet:
        events.append("verified")
        return artifacts

    calls: list[tuple[tuple[str, ...], Path, int]] = []

    def executed(
        command: tuple[str, ...], *, check: bool, cwd: Path, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        events.append(command[1] if command[0] == "/usr/bin/uv" else "import")
        calls.append((command, cwd, timeout))
        if len(calls) == 2:
            private_wheel = Path(command[-1])
            assert private_wheel != artifacts.wheel
            assert private_wheel.read_bytes() == artifacts.wheel.read_bytes()
            assert private_wheel.parent.name == "artifacts"
        return subprocess.CompletedProcess(command, 0)

    verify = mocker.patch(
        "scripts.release.verify_install.verify_release", side_effect=verified
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    mocker.patch("scripts.release.verify_install.subprocess.run", side_effect=executed)

    result = verify_clean_install(
        artifacts.wheel.parent,
        expected_name="md-converter",
        expected_version="0.1.0",
    )

    verify.assert_called_once_with(
        artifacts.wheel.parent,
        expected_name="md-converter",
        expected_version="0.1.0",
        manifest_name="release-integrity.json",
    )
    assert events == ["verified", "venv", "pip", "import"]
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
        (str(python), "-I", "-c", PUBLIC_IMPORT_CHECK, "0.1.0"),
        root,
        IMPORT_TIMEOUT_SECONDS,
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
    run = mocker.patch("scripts.release.verify_install.subprocess.run")

    with pytest.raises(ArtifactError, match="integrity failed"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="md-converter",
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
    run = mocker.patch("scripts.release.verify_install.subprocess.run")

    with pytest.raises(ArtifactError, match="changed before private copy"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="md-converter",
            expected_version="0.1.0",
        )

    run.assert_not_called()


@pytest.mark.parametrize("failing_call", [1, 2, 3])
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
        command: tuple[str, ...], *, check: bool, cwd: Path, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cwd)
        if len(calls) == failing_call:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    mocker.patch("scripts.release.verify_install.subprocess.run", side_effect=executed)

    with pytest.raises(ArtifactError, match="failed"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="md-converter",
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
        command: tuple[str, ...], *, check: bool, cwd: Path, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        roots.append(cwd)
        raise subprocess.TimeoutExpired(command, timeout)

    mocker.patch("scripts.release.verify_install.subprocess.run", side_effect=blocked)
    with pytest.raises(ArtifactError, match="timed out"):
        verify_clean_install(
            artifacts.wheel.parent,
            expected_name="md-converter",
            expected_version="0.1.0",
        )
    assert roots and not roots[0].exists()
