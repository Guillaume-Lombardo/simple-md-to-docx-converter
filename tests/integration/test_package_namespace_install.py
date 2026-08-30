"""Integration checks for clean retired-namespace package environments."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import markweave
from scripts.ci.check_package_namespace import check_namespace
from scripts.release.build import build_release

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]


def _run(*command: str, cwd: Path, environment: dict[str, str] | None = None) -> None:
    subprocess.run(
        command,
        check=True,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
    )


def _assert_no_retired_import(python: Path, *, environment: dict[str, str]) -> None:
    _run(
        str(python),
        "-c",
        (
            "from importlib.util import find_spec; import markweave; "
            "assert find_spec('md_converter') is None; "
            f"assert markweave.__version__ == {markweave.__version__!r}"
        ),
        cwd=python.parent,
        environment=environment,
    )


def test_clean_source_sdist_wheel_and_editable_installs_have_no_retired_namespace(
    tmp_path: Path,
) -> None:
    """All supported local package surfaces exclude ``md_converter``."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for package namespace integration checks")
    artifacts = build_release(
        tmp_path / "release",
        expected_name="markweave",
        expected_version=markweave.__version__,
        constraint=ROOT / "build-constraints.txt",
    )
    check_namespace(ROOT, (artifacts.wheel.parent,))
    clean_environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    source_environment = tmp_path / "source-environment"
    _run(uv, "venv", str(source_environment), "--python", "3.14", cwd=tmp_path)
    _assert_no_retired_import(
        source_environment / "bin" / "python", environment=clean_environment
    )
    for name, package in (
        ("sdist", artifacts.sdist),
        ("wheel", artifacts.wheel),
        ("editable", ROOT),
    ):
        environment = tmp_path / f"{name}-environment"
        python = environment / "bin" / "python"
        _run(uv, "venv", str(environment), "--python", "3.14", cwd=tmp_path)
        install = (uv, "pip", "install", "--python", str(python), "--no-deps")
        if name == "editable":
            install = (*install, "-e")
        _run(*install, str(package), cwd=tmp_path)
        _assert_no_retired_import(python, environment=os.environ.copy())
