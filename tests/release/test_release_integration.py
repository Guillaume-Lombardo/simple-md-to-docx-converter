"""Real boundary coverage for Python release artifacts and clean installation."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.release.artifacts import ArtifactError, verify_release
from scripts.release.build import build_release
from scripts.release.verify_install import verify_clean_install

pytestmark = pytest.mark.integration


def test_real_build_validation_clean_install_and_tamper_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real uv build imports publicly outside the tree and rejects changed bytes."""
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(project_root)
    output = tmp_path / "dist"

    built = build_release(
        output,
        expected_name="md-converter",
        expected_version="0.1.0",
        constraint=project_root / "build-constraints.txt",
    )
    verified = verify_release(
        output, expected_name="md-converter", expected_version="0.1.0"
    )
    installed = verify_clean_install(
        output, expected_name="md-converter", expected_version="0.1.0"
    )

    assert built.integrity == verified.integrity
    assert installed.wheel_name == verified.wheel.name
    assert installed.sha256 == verified.sha256_for(verified.wheel)

    tampered = tmp_path / "tampered"
    shutil.copytree(output, tampered)
    with (tampered / verified.wheel.name).open("ab") as stream:
        stream.write(b"controlled tamper")
    with pytest.raises(ArtifactError, match="integrity check failed"):
        verify_clean_install(
            tampered, expected_name="md-converter", expected_version="0.1.0"
        )
