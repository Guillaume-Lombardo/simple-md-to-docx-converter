"""Tests for the single-invocation release build wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release.artifacts import ArtifactError
from scripts.release.build import build_release

pytestmark = pytest.mark.unit


def test_build_release_invokes_uv_once_and_verifies(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """One uv build invocation is followed by manifest creation and verification."""
    output = tmp_path / "dist"
    mocker.patch("scripts.release.build.shutil.which", return_value="/usr/bin/uv")
    run = mocker.patch("scripts.release.build.subprocess.run")
    create = mocker.patch("scripts.release.build.create_manifest")
    verify = mocker.patch("scripts.release.build.verify_release")

    build_release(
        output,
        expected_name="md-converter",
        expected_version="0.1.0",
        constraint=Path("build-constraints.txt"),
    )

    run.assert_called_once_with(
        (
            "/usr/bin/uv",
            "build",
            "--out-dir",
            str(output),
            "--build-constraint",
            "build-constraints.txt",
            "--require-hashes",
        ),
        check=True,
    )
    create.assert_called_once_with(
        output, expected_name="md-converter", expected_version="0.1.0"
    )
    verify.assert_called_once_with(
        output, expected_name="md-converter", expected_version="0.1.0"
    )


def test_build_release_rejects_nonempty_output(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Existing output cannot be mixed with a new release build."""
    output = tmp_path / "dist"
    output.mkdir()
    (output / "old.whl").write_bytes(b"old")
    mocker.patch("scripts.release.build.shutil.which", return_value="/usr/bin/uv")
    run = mocker.patch("scripts.release.build.subprocess.run")

    with pytest.raises(ArtifactError, match="must be empty"):
        build_release(
            output,
            expected_name="md-converter",
            expected_version="0.1.0",
            constraint=Path("build-constraints.txt"),
        )

    run.assert_not_called()
