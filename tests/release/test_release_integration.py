"""Real boundary coverage for Python release artifacts and clean installation."""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from email.parser import BytesParser
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
        expected_name="markweave",
        expected_version="0.6.0",
        constraint=project_root / "build-constraints.txt",
    )
    verified = verify_release(
        output, expected_name="markweave", expected_version="0.6.0"
    )
    installed = verify_clean_install(
        output, expected_name="markweave", expected_version="0.6.0"
    )

    assert built.integrity == verified.integrity
    assert installed.wheel_name == verified.wheel.name
    assert installed.sha256 == verified.sha256_for(verified.wheel)
    with zipfile.ZipFile(verified.wheel) as wheel:
        names = set(wheel.namelist())
        assert "markweave/__init__.py" in names
        package_init = wheel.read("markweave/__init__.py").decode()
        assert "create_app" not in package_init
        assert '__all__ = ["__version__"]' in package_init
        entry_points = wheel.read("markweave-0.6.0.dist-info/entry_points.txt").decode()
        assert (
            entry_points == "[console_scripts]\nmarkweave = markweave.cli.main:main\n"
        )
        assert not any(name.startswith("md_converter/") for name in names)
        assert "md_converter.py" not in names
        assert not any(
            "/purelib/md_converter" in name or "/platlib/md_converter" in name
            for name in names
        )
        assert "markweave-0.6.0.dist-info/licenses/LICENSE" in names
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(wheel.read(metadata_name))
        assert metadata["License-Expression"] == "Apache-2.0"
        assert metadata.get_all("Provides-Extra") == [
            "all",
            "distributed",
            "server",
            "standalone",
        ]
        requirements = metadata.get_all("Requires-Dist", [])
        assert "boto3<2,>=1.43.82; extra == 'distributed'" in requirements
        assert "psycopg[binary]<4,>=3.3.4; extra == 'distributed'" in requirements
        assert not any(
            "boto3" in requirement and "extra == 'server'" in requirement
            for requirement in requirements
        )
        assert not any(
            "psycopg" in requirement and "extra == 'server'" in requirement
            for requirement in requirements
        )
    with tarfile.open(verified.sdist, mode="r:gz") as sdist:
        names = set(sdist.getnames())
        assert "markweave-0.6.0/LICENSE" in names
        assert "markweave-0.6.0/src/markweave/__init__.py" in names
        assert not any("/src/md_converter/" in name for name in names)
        assert not any(name.endswith("/src/md_converter.py") for name in names)

    tampered = tmp_path / "tampered"
    shutil.copytree(output, tampered)
    with (tampered / verified.wheel.name).open("ab") as stream:
        stream.write(b"controlled tamper")
    with pytest.raises(ArtifactError, match="integrity check failed"):
        verify_clean_install(
            tampered, expected_name="markweave", expected_version="0.6.0"
        )
