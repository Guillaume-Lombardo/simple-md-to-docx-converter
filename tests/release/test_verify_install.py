"""Tests for clean installation of a manifest-bound wheel copy."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import markweave
from scripts.release.artifacts import ArtifactError, ArtifactSet
from scripts.release.verify_install import (
    BASE_FORBIDDEN_MODULES,
    BASE_ISOLATION_CHECK,
    BASE_RECOVERY_CHECK,
    CONSOLE_TIMEOUT_SECONDS,
    DISTRIBUTED_RECOVERY_CHECK,
    ENVIRONMENT_TIMEOUT_SECONDS,
    EXTRA_IMPORT_CHECK,
    IMPORT_TIMEOUT_SECONDS,
    INSTALL_TIMEOUT_SECONDS,
    PUBLIC_IMPORT_CHECK,
    STANDALONE_RECOVERY_CHECK,
    SUPPORTED_INSTALLATION_PROFILES,
    verify_clean_install,
    verify_final_image_dependency_union,
)

pytestmark = pytest.mark.unit


def test_base_recovery_check_is_valid_isolated_python() -> None:
    """The real-wheel recovery contract must remain an executable verifier."""
    compile(BASE_RECOVERY_CHECK, "<base-recovery-check>", "exec")


@pytest.mark.parametrize(
    ("script", "name"),
    (
        (STANDALONE_RECOVERY_CHECK, "standalone-recovery-check"),
        (DISTRIBUTED_RECOVERY_CHECK, "distributed-recovery-check"),
    ),
)
def test_profile_recovery_checks_are_valid_isolated_python(
    script: str, name: str
) -> None:
    """Profile-specific real-wheel recovery checks remain executable."""
    compile(script, f"<{name}>", "exec")


def test_public_import_check_rejects_legacy_import_after_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The isolated verification script checks both the new and removed imports."""
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: ModuleSpec(name, loader=None)
    )
    monkeypatch.setattr(sys, "argv", ["check", "markweave", "0.5.0"])

    with pytest.raises(
        SystemExit, match="legacy md_converter import remains installed"
    ):
        exec(PUBLIC_IMPORT_CHECK, {})  # noqa: S102 - isolated verifier contract


def test_public_import_check_rejects_application_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distribution and public application versions must identify one release."""
    monkeypatch.setattr(markweave, "__version__", "9.9.9")
    monkeypatch.setattr(sys, "argv", ["check", "markweave", "0.5.0"])

    with pytest.raises(SystemExit, match=r"unexpected markweave\.__version__"):
        exec(PUBLIC_IMPORT_CHECK, {})  # noqa: S102 - isolated verifier contract


def test_base_isolation_check_rejects_an_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base CLI package remains free of all backend dependency roots."""
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: ModuleSpec(name, loader=None)
    )
    monkeypatch.setattr(sys, "argv", ["check", *BASE_FORBIDDEN_MODULES])

    with pytest.raises(SystemExit, match="unexpectedly contains optional dependency"):
        exec(BASE_ISOLATION_CHECK, {})  # noqa: S102 - isolated verifier contract


def test_extra_import_check_rejects_a_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every supported optional profile proves its declared dependency roots exist."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(sys, "argv", ["check", "fastapi"])

    with pytest.raises(SystemExit, match="missing required optional dependency"):
        exec(EXTRA_IMPORT_CHECK, {})  # noqa: S102 - isolated verifier contract


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
    expected_events = ["verified"]
    for profile in SUPPORTED_INSTALLATION_PROFILES:
        expected_events.extend(
            [
                f"clean {profile.name} environment creation",
                f"exact {profile.name} wheel installation",
                f"isolated {profile.name} public import check",
                (
                    "base optional dependency isolation check"
                    if profile.name == "base"
                    else f"isolated {profile.name} optional dependency check"
                ),
                *(
                    ["base recovery optional dependency error check"]
                    if profile.name == "base"
                    else []
                ),
                *(
                    ["standalone recovery success and S3 isolation check"]
                    if profile.name == "standalone"
                    else []
                ),
                *(
                    [f"{profile.name} recovery S3 dependency check"]
                    if profile.name in {"distributed", "all"}
                    else []
                ),
                f"isolated {profile.name} console version check",
                f"isolated {profile.name} console help check",
            ]
        )
    assert events == expected_events
    venv_command, root, timeout = calls[0]
    environment = root / "venv-base"
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
    assert calls[3] == (
        (
            str(python),
            "-I",
            "-c",
            BASE_ISOLATION_CHECK,
            *BASE_FORBIDDEN_MODULES,
        ),
        root,
        IMPORT_TIMEOUT_SECONDS,
    )
    console = environment / "bin" / "markweave"
    assert calls[4] == (
        (str(python), "-I", "-c", BASE_RECOVERY_CHECK),
        root,
        CONSOLE_TIMEOUT_SECONDS,
    )
    assert calls[5] == (
        (str(python), "-I", str(console), "--version"),
        root,
        CONSOLE_TIMEOUT_SECONDS,
    )
    assert calls[6] == (
        (str(python), "-I", str(console), "--help"),
        root,
        CONSOLE_TIMEOUT_SECONDS,
    )
    standalone_python = root / "venv-standalone" / "bin" / "python"
    assert calls[17] == (
        (str(standalone_python), "-I", "-c", STANDALONE_RECOVERY_CHECK),
        root,
        CONSOLE_TIMEOUT_SECONDS,
    )
    distributed_install = calls[21]
    assert distributed_install[0][-1] == f"{private_wheel}[distributed]"
    assert distributed_install[2] == INSTALL_TIMEOUT_SECONDS
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


@pytest.mark.parametrize("failing_call", range(1, 35))
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


def test_final_image_dependency_union_requires_the_all_extra(tmp_path: Path) -> None:
    """The final image must not accidentally fall back to the base CLI install."""
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text(
        "RUN uv sync --locked --no-dev --no-editable\n", encoding="utf-8"
    )

    with pytest.raises(ArtifactError, match=r"markweave\[all\]"):
        verify_final_image_dependency_union(tmp_path)

    containerfile.write_text(
        "RUN uv sync --locked --no-dev --no-editable --extra all\n", encoding="utf-8"
    )
    verify_final_image_dependency_union(tmp_path)
