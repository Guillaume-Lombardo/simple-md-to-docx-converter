"""Tests for transactional single-invocation release builds."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release.artifacts import ArtifactError, ArtifactSet
from scripts.release.build import (
    BUILD_TIMEOUT_SECONDS,
    _publish_no_replace,
    build_release,
)

pytestmark = pytest.mark.unit


def _verified(directory: Path) -> ArtifactSet:
    return ArtifactSet(
        wheel=directory / "md_converter-0.1.0-py3-none-any.whl",
        sdist=directory / "md_converter-0.1.0.tar.gz",
        integrity=(("md_converter-0.1.0-py3-none-any.whl", "a" * 64),),
    )


def _publish(staged: Path, output: Path) -> None:
    staged.rename(output)


def test_build_release_stages_once_then_publishes_atomically(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """The reviewed build is private until validation and one no-replace rename."""
    output = tmp_path / "dist"
    events: list[tuple[str, Path]] = []
    mocker.patch("scripts.release.build.shutil.which", return_value="/usr/bin/uv")
    run = mocker.patch("scripts.release.build.subprocess.run")

    def create(directory: Path, **kwargs: str) -> Path:
        events.append(("manifest", directory))
        return directory / "release-integrity.json"

    def verify(directory: Path, **kwargs: str) -> ArtifactSet:
        events.append(("verify", directory))
        return _verified(directory)

    mocker.patch("scripts.release.build.create_manifest", side_effect=create)
    mocker.patch("scripts.release.build.verify_release", side_effect=verify)

    def publish(staged: Path, target: Path) -> None:
        events.append(("publish", staged))
        _publish(staged, target)

    mocker.patch("scripts.release.build._publish_no_replace", side_effect=publish)

    result = build_release(
        output,
        expected_name="md-converter",
        expected_version="0.1.0",
        constraint=Path("build-constraints.txt"),
    )

    run.assert_called_once()
    command = run.call_args.args[0]
    staged = Path(command[3])
    assert staged.name == "release"
    assert staged.parent.name.startswith(".dist-staging-")
    assert command == (
        "/usr/bin/uv",
        "build",
        "--out-dir",
        str(staged),
        "--build-constraint",
        str(Path("build-constraints.txt").resolve()),
        "--require-hashes",
    )
    assert run.call_args.kwargs == {
        "check": True,
        "cwd": Path.cwd(),
        "timeout": BUILD_TIMEOUT_SECONDS,
    }
    assert [name for name, _ in events] == ["manifest", "verify", "publish"]
    assert all(directory == staged for _, directory in events)
    assert output.is_dir()
    assert result.wheel == output / "md_converter-0.1.0-py3-none-any.whl"
    assert list(tmp_path.glob(".dist-staging-*")) == []


def test_build_release_rejects_any_existing_output(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Even an empty target cannot be replaced by publication."""
    output = tmp_path / "dist"
    output.mkdir()
    run = mocker.patch("scripts.release.build.subprocess.run")

    with pytest.raises(ArtifactError, match="already exists"):
        build_release(
            output,
            expected_name="md-converter",
            expected_version="0.1.0",
            constraint=Path("build-constraints.txt"),
        )

    run.assert_not_called()


def test_publication_race_preserves_target_and_cleans_staging(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """A target appearing after validation is preserved and staging is removed."""
    output = tmp_path / "dist"
    mocker.patch("scripts.release.build.shutil.which", return_value="/usr/bin/uv")
    mocker.patch("scripts.release.build.subprocess.run")
    mocker.patch("scripts.release.build.create_manifest")
    mocker.patch(
        "scripts.release.build.verify_release",
        side_effect=lambda directory, **kwargs: _verified(directory),
    )

    def race(staged: Path, target: Path) -> None:
        target.mkdir()
        (target / "owner-data").write_text("preserve")
        raise ArtifactError(f"output path already exists: {target}")

    mocker.patch("scripts.release.build._publish_no_replace", side_effect=race)

    with pytest.raises(ArtifactError, match="already exists"):
        build_release(
            output,
            expected_name="md-converter",
            expected_version="0.1.0",
            constraint=Path("build-constraints.txt"),
        )

    assert (output / "owner-data").read_text() == "preserve"
    assert list(tmp_path.glob(".dist-staging-*")) == []


def test_atomic_no_replace_primitive_preserves_existing_target(tmp_path: Path) -> None:
    """The real publication syscall never replaces even an empty target."""
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "artifact").write_text("new")
    output = tmp_path / "dist"
    output.mkdir()

    with pytest.raises(ArtifactError, match="already exists"):
        _publish_no_replace(staged, output)

    assert staged.is_dir()
    assert output.is_dir()
    assert not (output / "artifact").exists()


def test_timeout_is_normalized_cleanup_allows_retry(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """A timed-out build leaves no target or staging and a later retry can publish."""
    output = tmp_path / "dist"
    mocker.patch("scripts.release.build.shutil.which", return_value="/usr/bin/uv")
    run = mocker.patch(
        "scripts.release.build.subprocess.run",
        side_effect=subprocess.TimeoutExpired(("uv", "build"), 600),
    )

    with pytest.raises(ArtifactError, match="build timed out"):
        build_release(
            output,
            expected_name="md-converter",
            expected_version="0.1.0",
            constraint=Path("build-constraints.txt"),
        )

    assert not output.exists()
    assert list(tmp_path.glob(".dist-staging-*")) == []

    run.side_effect = None
    mocker.patch("scripts.release.build.create_manifest")
    mocker.patch(
        "scripts.release.build.verify_release",
        side_effect=lambda directory, **kwargs: _verified(directory),
    )
    mocker.patch("scripts.release.build._publish_no_replace", side_effect=_publish)
    build_release(
        output,
        expected_name="md-converter",
        expected_version="0.1.0",
        constraint=Path("build-constraints.txt"),
    )
    assert output.is_dir()
    assert run.call_count == 2
