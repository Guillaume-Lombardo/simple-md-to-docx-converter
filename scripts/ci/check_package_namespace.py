"""Reject retired Python namespaces and stale distribution artifacts.

Run ``uv run python -m scripts.ci.check_package_namespace`` in a checkout.  To
remove only the known ignored legacy cache tree and ``dist/md_converter-*``
release files, first inspect the planned paths with ``--cleanup-legacy-artifacts
--dry-run`` and then rerun without ``--dry-run``.  The cleanup refuses any
legacy source tree that contains files other than bytecode caches.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

from scripts.release.artifacts import ArtifactError, verify_release

PUBLIC_NAMESPACE = "markweave"
RETIRED_NAMESPACE = "md_converter"
LEGACY_DIST_PREFIXES = ("md_converter-", "md-converter-")
BUILD_OUTPUT_DIRECTORIES = frozenset({"artifacts", "build", "dist"})


class NamespaceError(ValueError):
    """The checkout or supplied package artifacts contain stale namespace data."""


def _project_identity(root: Path) -> tuple[str, str]:
    try:
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        name = metadata["project"]["name"]
        version = metadata["project"]["version"]
    except (KeyError, OSError, tomllib.TOMLDecodeError) as error:
        raise NamespaceError(f"cannot read project identity: {error}") from error
    if not isinstance(name, str) or not isinstance(version, str):
        raise NamespaceError("project identity must contain string name and version")
    return name, version


def _tracked_paths(root: Path) -> tuple[PurePosixPath, ...]:
    completed = subprocess.run(  # noqa: S603 - fixed Git query
        ("git", "-C", str(root), "ls-files", "-z"),  # noqa: S607
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise NamespaceError("cannot inspect tracked repository paths")
    return tuple(
        PurePosixPath(path.decode("utf-8"))
        for path in completed.stdout.split(b"\0")
        if path
    )


def _check_source_namespaces(root: Path) -> None:
    source = root / "src"
    if not source.is_dir() or source.is_symlink():
        raise NamespaceError("src must be a real directory")
    namespaces = {
        path.name
        for path in source.iterdir()
        if not path.name.startswith(".")
        and (path.is_dir() or path.suffix in {".py", ".pyi"})
    }
    expected = {PUBLIC_NAMESPACE}
    if namespaces != expected:
        rendered = ", ".join(sorted(namespaces)) or "none"
        raise NamespaceError(
            f"unexpected source namespaces: {rendered}; expected {PUBLIC_NAMESPACE}"
        )


def _check_tracked_outputs(root: Path) -> None:
    violations: list[str] = []
    for path in _tracked_paths(root):
        parts = path.parts
        if (
            "__pycache__" in parts
            or path.suffix in {".pyc", ".pyo"}
            or (parts and parts[0] in BUILD_OUTPUT_DIRECTORIES)
        ):
            violations.append(str(path))
    if violations:
        raise NamespaceError(
            "tracked bytecode or build output: " + ", ".join(sorted(violations))
        )


def _check_version_source(root: Path, expected_version: str) -> None:
    namespace_version = root / "src" / PUBLIC_NAMESPACE / "version.py"
    namespace = {"__builtins__": {}}
    try:
        exec(namespace_version.read_text(encoding="utf-8"), namespace)  # noqa: S102
    except OSError as error:
        raise NamespaceError(
            f"cannot read public namespace version: {error}"
        ) from error
    if namespace.get("VERSION") != expected_version:
        raise NamespaceError("public namespace version does not match pyproject.toml")


def _check_artifact_directory(
    directory: Path, *, expected_name: str, expected_version: str
) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise NamespaceError(f"invalid artifact directory: {directory}")
    stale = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name.startswith(LEGACY_DIST_PREFIXES)
    )
    if stale:
        raise NamespaceError("stale legacy package artifacts: " + ", ".join(stale))
    try:
        verify_release(
            directory, expected_name=expected_name, expected_version=expected_version
        )
    except ArtifactError as error:
        raise NamespaceError(f"invalid package artifacts: {error}") from error


def check_namespace(root: Path, artifact_directories: tuple[Path, ...] = ()) -> None:
    """Validate source, tracked files, version identity, and release artifacts."""
    resolved_root = root.resolve(strict=True)
    expected_name, expected_version = _project_identity(resolved_root)
    _check_source_namespaces(resolved_root)
    _check_version_source(resolved_root, expected_version)
    _check_tracked_outputs(resolved_root)
    for directory in artifact_directories:
        _check_artifact_directory(
            directory.resolve(strict=True),
            expected_name=expected_name,
            expected_version=expected_version,
        )


def _legacy_cache_tree(root: Path) -> Path | None:
    tree = root / "src" / RETIRED_NAMESPACE
    if not tree.exists() and not tree.is_symlink():
        return None
    if tree.is_symlink() or not tree.is_dir():
        raise NamespaceError(f"refusing unsafe legacy cache path: {tree}")
    for path in tree.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.suffix != ".pyc"):
            raise NamespaceError(f"refusing non-bytecode legacy cache path: {path}")
    return tree


def legacy_cleanup_paths(root: Path) -> tuple[Path, ...]:
    """Return only legacy cache and distribution files safe to remove."""
    resolved_root = root.resolve(strict=True)
    paths: list[Path] = []
    cache_tree = _legacy_cache_tree(resolved_root)
    if cache_tree is not None:
        paths.append(cache_tree)
    dist = resolved_root / "dist"
    if dist.is_dir() and not dist.is_symlink():
        paths.extend(
            sorted(
                path
                for path in dist.iterdir()
                if path.is_file()
                and path.name.startswith(LEGACY_DIST_PREFIXES)
                and path.suffix in {".whl", ".gz"}
            )
        )
    return tuple(paths)


def cleanup_legacy_artifacts(root: Path, *, dry_run: bool) -> tuple[Path, ...]:
    """Remove the exact legacy artifacts after validating their safe shapes."""
    paths = legacy_cleanup_paths(root)
    if not dry_run:
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    return paths


def main(argv: list[str] | None = None) -> int:
    """Run namespace checks or the documented, constrained cleanup operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact-dir", type=Path, action="append", default=[])
    parser.add_argument("--cleanup-legacy-artifacts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cleanup_legacy_artifacts:
            for path in cleanup_legacy_artifacts(args.root, dry_run=args.dry_run):
                print(path)
            if args.dry_run:
                return 0
        check_namespace(args.root, tuple(args.artifact_dir))
    except (NamespaceError, OSError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - command-line entry point
    raise SystemExit(main())
