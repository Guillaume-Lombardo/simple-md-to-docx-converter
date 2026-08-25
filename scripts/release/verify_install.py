"""Install and import a verified wheel in a fresh uv-managed environment."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.release.artifacts import MANIFEST_NAME, ArtifactError, verify_release

PUBLIC_IMPORT_CHECK = """\
from importlib.metadata import version
import sys

installed = version("md-converter")
if installed != sys.argv[1]:
    raise SystemExit(f"unexpected installed version: {installed}")
from md_converter import create_app
if not callable(create_app):
    raise SystemExit("md_converter.create_app is not callable")
"""


def _run(command: tuple[str, ...], *, cwd: Path, label: str) -> None:
    try:
        subprocess.run(  # noqa: S603 - argv only, with no shell interpretation
            command,
            check=True,
            cwd=cwd,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ArtifactError(f"{label} failed") from error


def verify_clean_install(
    directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    manifest_name: str = MANIFEST_NAME,
) -> None:
    """Verify integrity, then install and import the exact wheel in isolation."""
    artifacts = verify_release(
        directory,
        expected_name=expected_name,
        expected_version=expected_version,
        manifest_name=manifest_name,
    )
    wheel = artifacts.wheel.resolve(strict=True)
    uv = shutil.which("uv")
    if uv is None:
        raise ArtifactError("uv executable was not found")

    with tempfile.TemporaryDirectory(prefix="md-converter-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        python = environment / "bin" / "python"
        _run(
            (
                uv,
                "venv",
                "--python",
                "3.14",
                "--no-project",
                str(environment),
            ),
            cwd=root,
            label="clean environment creation",
        )
        _run(
            (
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--strict",
                str(wheel),
            ),
            cwd=root,
            label="exact wheel installation",
        )
        _run(
            (str(python), "-I", "-c", PUBLIC_IMPORT_CHECK, expected_version),
            cwd=root,
            label="isolated public import check",
        )


def main(argv: list[str] | None = None) -> int:
    """Verify an exact wheel through a clean installation and public import."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest-name", default=MANIFEST_NAME)
    args = parser.parse_args(argv)
    try:
        verify_clean_install(
            args.directory,
            expected_name=args.name,
            expected_version=args.version,
            manifest_name=args.manifest_name,
        )
    except ArtifactError as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI
    raise SystemExit(main())
