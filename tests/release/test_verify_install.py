"""Tests for clean installation of a verified wheel."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release.artifacts import ArtifactError, ArtifactSet
from scripts.release.verify_install import PUBLIC_IMPORT_CHECK, verify_clean_install

pytestmark = pytest.mark.unit


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactSet:
    """Provide exact artifact paths returned by prior integrity verification."""
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = directory / "md_converter-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"verified wheel")
    sdist = directory / "md_converter-0.1.0.tar.gz"
    sdist.write_bytes(b"verified sdist")
    return ArtifactSet(wheel=wheel, sdist=sdist)


def test_clean_install_verifies_before_using_exact_wheel_and_cleans_up(
    artifacts: ArtifactSet, mocker: MockerFixture
) -> None:
    """Integrity precedes one exact install and an isolated import outside the repo."""
    events: list[str] = []

    def verified(*args: object, **kwargs: object) -> ArtifactSet:
        events.append("verified")
        return artifacts

    calls: list[tuple[tuple[str, ...], Path]] = []

    def executed(
        command: tuple[str, ...], *, check: bool, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        events.append(command[1] if command[0] == "/usr/bin/uv" else "import")
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0)

    verify = mocker.patch(
        "scripts.release.verify_install.verify_release", side_effect=verified
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    mocker.patch("scripts.release.verify_install.subprocess.run", side_effect=executed)

    verify_clean_install(
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
    venv_command, root = calls[0]
    environment = root / "venv"
    python = environment / "bin" / "python"
    assert venv_command == (
        "/usr/bin/uv",
        "venv",
        "--python",
        "3.14",
        "--no-project",
        str(environment),
    )
    assert calls[1] == (
        (
            "/usr/bin/uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--strict",
            str(artifacts.wheel.resolve()),
        ),
        root,
    )
    assert calls[2] == (
        (str(python), "-I", "-c", PUBLIC_IMPORT_CHECK, "0.1.0"),
        root,
    )
    assert all(cwd == root for _, cwd in calls)
    assert root != Path.cwd()
    assert not root.exists()


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


@pytest.mark.parametrize("failing_call", [1, 2, 3])
def test_subprocess_failure_stops_later_steps_and_cleans_up(
    artifacts: ArtifactSet, mocker: MockerFixture, failing_call: int
) -> None:
    """Each external failure is closed, aborts later work, and removes the venv."""
    mocker.patch(
        "scripts.release.verify_install.verify_release", return_value=artifacts
    )
    mocker.patch(
        "scripts.release.verify_install.shutil.which", return_value="/usr/bin/uv"
    )
    calls: list[Path] = []

    def executed(
        command: tuple[str, ...], *, check: bool, cwd: Path
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
