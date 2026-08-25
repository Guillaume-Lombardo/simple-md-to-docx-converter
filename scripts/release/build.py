"""Build one wheel and one sdist, then create their integrity manifest."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from scripts.release.artifacts import ArtifactError, create_manifest, verify_release


def build_release(
    output: Path,
    *,
    expected_name: str,
    expected_version: str,
    constraint: Path,
) -> None:
    """Build exactly once into an explicit empty directory and verify the result."""
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ArtifactError(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv is None:
        raise ArtifactError("uv executable was not found")
    subprocess.run(  # noqa: S603 - fixed executable and shell=False prevent injection
        (
            uv,
            "build",
            "--out-dir",
            str(output),
            "--build-constraint",
            str(constraint),
            "--require-hashes",
        ),
        check=True,
    )
    create_manifest(
        output,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    verify_release(
        output,
        expected_name=expected_name,
        expected_version=expected_version,
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
