"""Build, verify, and transactionally publish one wheel and one sdist."""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.release.artifacts import (
    ArtifactError,
    ArtifactSet,
    create_manifest,
    verify_release,
)

BUILD_TIMEOUT_SECONDS = 600
AT_FDCWD = -100
RENAME_NOREPLACE = 1


def _run_build(command: tuple[str, ...], *, cwd: Path) -> None:
    try:
        subprocess.run(  # noqa: S603 - argv only, with no shell interpretation
            command,
            check=True,
            cwd=cwd,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ArtifactError("release build timed out") from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise ArtifactError("release build failed") from error


def _publish_no_replace(staged: Path, output: Path) -> None:
    """Atomically rename a staged directory only when the target is absent."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise ArtifactError("atomic no-replace publication is unavailable") from error
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD,
        os.fsencode(staged),
        AT_FDCWD,
        os.fsencode(output),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise ArtifactError(f"output path already exists: {output}")
    raise ArtifactError(
        f"atomic release publication failed: {os.strerror(error_number)}"
    )


def build_release(
    output: Path,
    *,
    expected_name: str,
    expected_version: str,
    constraint: Path,
) -> ArtifactSet:
    """Build once in private staging, verify, then publish atomically."""
    try:
        parent = output.parent.resolve(strict=True)
        constraint = constraint.resolve(strict=True)
    except OSError as error:
        raise ArtifactError(f"invalid release build path: {error}") from error
    if not parent.is_dir() or parent.is_symlink():
        raise ArtifactError(f"invalid output parent: {parent}")
    if output.exists() or output.is_symlink():
        raise ArtifactError(f"output path already exists: {output}")
    uv = shutil.which("uv")
    if uv is None:
        raise ArtifactError("uv executable was not found")
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}-staging-", dir=parent
        ) as temporary:
            staging_root = Path(temporary)
            staged_release = staging_root / "release"
            staged_release.mkdir(mode=0o700)
            _run_build(
                (
                    uv,
                    "build",
                    "--out-dir",
                    str(staged_release),
                    "--build-constraint",
                    str(constraint),
                    "--require-hashes",
                ),
                cwd=Path.cwd(),
            )
            create_manifest(
                staged_release,
                expected_name=expected_name,
                expected_version=expected_version,
            )
            verified = verify_release(
                staged_release,
                expected_name=expected_name,
                expected_version=expected_version,
            )
            _publish_no_replace(staged_release, output)
    except ArtifactError:
        raise
    except (OSError, ValueError) as error:
        raise ArtifactError(f"release build transaction failed: {error}") from error

    return ArtifactSet(
        wheel=output / verified.wheel.name,
        sdist=output / verified.sdist.name,
        integrity=verified.integrity,
    )


def main(argv: list[str] | None = None) -> int:
    """Build and validate release artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--constraint", type=Path, default=Path("build-constraints.txt")
    )
    args = parser.parse_args(argv)
    try:
        build_release(
            args.output,
            expected_name=args.name,
            expected_version=args.version,
            constraint=args.constraint,
        )
    except ArtifactError as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI
    raise SystemExit(main())
