"""Install and import a manifest-bound wheel in a fresh uv environment."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.release.artifacts import (
    MANIFEST_NAME,
    MAX_ARTIFACT_BYTES,
    READ_CHUNK_BYTES,
    ArtifactError,
    verify_release,
)

ENVIRONMENT_TIMEOUT_SECONDS = 120
INSTALL_TIMEOUT_SECONDS = 300
IMPORT_TIMEOUT_SECONDS = 60

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


@dataclass(frozen=True)
class CleanInstallResult:
    """Digest linkage between the installed copy and publishable wheel."""

    wheel_name: str
    sha256: str


def _run(command: tuple[str, ...], *, cwd: Path, label: str, timeout: int) -> None:
    try:
        subprocess.run(  # noqa: S603 - argv only, with no shell interpretation
            command,
            check=True,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ArtifactError(f"{label} timed out") from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise ArtifactError(f"{label} failed") from error


def _copy_manifest_bound_wheel(source: Path, destination: Path, digest: str) -> None:
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as error:
        raise ArtifactError(f"cannot reopen verified wheel: {error}") from error
    destination_descriptor: int | None = None
    try:
        source_metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_metadata.st_mode):
            raise ArtifactError("verified wheel is no longer a regular file")
        if source_metadata.st_size > MAX_ARTIFACT_BYTES:
            raise ArtifactError("verified wheel exceeds the size limit")
        destination_descriptor = os.open(destination, destination_flags, 0o400)
        copied_digest = hashlib.sha256()
        copied_size = 0
        while chunk := os.read(source_descriptor, READ_CHUNK_BYTES):
            copied_size += len(chunk)
            if copied_size > MAX_ARTIFACT_BYTES:
                raise ArtifactError("verified wheel changed beyond the size limit")
            copied_digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise ArtifactError("private wheel copy made no progress")
                view = view[written:]
        os.fsync(destination_descriptor)
        if (
            copied_size != source_metadata.st_size
            or copied_digest.hexdigest() != digest
        ):
            raise ArtifactError("verified wheel changed before private copy completed")
    except OSError as error:
        raise ArtifactError(f"private wheel copy failed: {error}") from error
    finally:
        os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def verify_clean_install(
    directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    manifest_name: str = MANIFEST_NAME,
) -> CleanInstallResult:
    """Verify integrity, copy by digest, then install and import in isolation."""
    artifacts = verify_release(
        directory,
        expected_name=expected_name,
        expected_version=expected_version,
        manifest_name=manifest_name,
    )
    wheel_digest = artifacts.sha256_for(artifacts.wheel)
    uv = shutil.which("uv")
    if uv is None:
        raise ArtifactError("uv executable was not found")

    with tempfile.TemporaryDirectory(prefix="md-converter-wheel-") as temporary:
        root = Path(temporary)
        private_artifacts = root / "artifacts"
        private_artifacts.mkdir(mode=0o700)
        private_wheel = private_artifacts / artifacts.wheel.name
        _copy_manifest_bound_wheel(artifacts.wheel, private_wheel, wheel_digest)
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
            timeout=ENVIRONMENT_TIMEOUT_SECONDS,
        )
        _run(
            (
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--strict",
                str(private_wheel),
            ),
            cwd=root,
            label="exact wheel installation",
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
        _run(
            (str(python), "-I", "-c", PUBLIC_IMPORT_CHECK, expected_version),
            cwd=root,
            label="isolated public import check",
            timeout=IMPORT_TIMEOUT_SECONDS,
        )
    return CleanInstallResult(wheel_name=artifacts.wheel.name, sha256=wheel_digest)


def main(argv: list[str] | None = None) -> int:
    """Verify an exact wheel through a clean installation and public import."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest-name", default=MANIFEST_NAME)
    args = parser.parse_args(argv)
    try:
        result = verify_clean_install(
            args.directory,
            expected_name=args.name,
            expected_version=args.version,
            manifest_name=args.manifest_name,
        )
    except ArtifactError as error:
        parser.exit(1, f"error: {error}\n")
    print(f"verified-wheel-sha256={result.sha256} wheel={result.wheel_name}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI
    raise SystemExit(main())
