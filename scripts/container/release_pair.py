"""Create and verify the manifest binding a Markweave release image pair."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from scripts.container.integrity import sha256_file
from scripts.container.recover_release_evidence import (
    RecoveryEvidenceError,
    verify_recovery_evidence,
)

PAIR_MANIFEST = "release-images.json"
PAIR_CHECKSUMS = "release-images.sha256"
IMAGE_DIRECTORIES = {
    "backend": "ghcr.io/guillaume-lombardo/md-converter",
    "frontend": "ghcr.io/guillaume-lombardo/md-converter-web",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_MANIFEST_BYTES = 1_048_576


class ReleasePairError(ValueError):
    """The staged or retained release pair is invalid."""


def _safe_object(path: Path) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_MANIFEST_BYTES
    ):
        raise ReleasePairError(f"unsafe release-pair file: {path.name}")
    try:
        value: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleasePairError(f"invalid release-pair file: {path.name}") from error
    if not isinstance(value, dict):
        raise ReleasePairError(f"release-pair file is not an object: {path.name}")
    return value


def _receipt(directory: Path) -> dict[str, Any]:
    return _safe_object(directory / "registry-publication.json")


def _relative_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("*")
                if path.name != PAIR_CHECKSUMS and path.is_file()
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


def _verify_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ReleasePairError("release-pair directory is unsafe")
    top_level = tuple(root.iterdir())
    allowed = {*IMAGE_DIRECTORIES, PAIR_MANIFEST, PAIR_CHECKSUMS}
    if {path.name for path in top_level} - allowed:
        raise ReleasePairError("release-pair tree contains an unexpected entry")
    for role in IMAGE_DIRECTORIES:
        directory = root / role
        if directory.is_symlink() or not directory.is_dir():
            raise ReleasePairError(f"{role} evidence directory is unsafe")
    all_paths = tuple(root.rglob("*"))
    if any(
        path.is_symlink() or (not path.is_file() and not path.is_dir())
        for path in all_paths
    ):
        raise ReleasePairError("release-pair tree contains an unsafe entry")
    paths = _relative_files(root)
    if {path.relative_to(root).parts[0] for path in paths} - {
        *IMAGE_DIRECTORIES,
        PAIR_MANIFEST,
    }:
        raise ReleasePairError("release-pair tree contains an unexpected entry")


def create_release_pair(
    root: Path, *, version: str, source_sha: str, frontend_lock: Path
) -> dict[str, Any]:
    """Bind the two verified image receipts and frontend lock into one manifest."""
    _verify_tree(root)
    if not frontend_lock.is_file() or frontend_lock.is_symlink():
        raise ReleasePairError("frontend lockfile is unsafe")
    images: dict[str, Any] = {}
    for role, package in IMAGE_DIRECTORIES.items():
        receipt = _receipt(root / role)
        if (receipt.get("version"), receipt.get("source_sha")) != (
            version,
            source_sha,
        ):
            raise ReleasePairError(f"{role} receipt identity differs from the release")
        digest = receipt.get("registry_manifest_digest")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        ):
            raise ReleasePairError(f"{role} receipt digest is invalid")
        images[role] = {
            "bundle_manifest_sha256": sha256_file(
                root / role / "release-bundle.sha256"
            ),
            "package": package,
            "receipt_sha256": sha256_file(root / role / "registry-publication.json"),
            "registry_manifest_digest": digest,
        }
    manifest = {
        "frontend_lock_sha256": sha256_file(frontend_lock),
        "images": images,
        "schema_version": 1,
        "source_sha": source_sha,
        "version": version,
    }
    (root / PAIR_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in _relative_files(root)
    ]
    (root / PAIR_CHECKSUMS).write_text("\n".join(lines) + "\n", encoding="ascii")
    return manifest


def verify_release_pair(  # noqa: PLR0913 - every release identity is explicit
    root: Path,
    *,
    version: str,
    tag: str,
    source_sha: str,
    frontend_lock_sha256: str,
    backend_registry_digest: str,
    frontend_registry_digest: str,
) -> None:
    """Verify exact retained bytes and both public publication identities."""
    _verify_tree(root)
    checksums = root / PAIR_CHECKSUMS
    if checksums.is_symlink() or not checksums.is_file():
        raise ReleasePairError("release-pair checksum manifest is unsafe")
    expected_paths = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in _relative_files(root)
    }
    parsed: dict[str, str] = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or SHA256.fullmatch(digest) is None or name in parsed:
            raise ReleasePairError("release-pair checksum manifest is invalid")
        parsed[name] = digest
    if parsed != expected_paths:
        raise ReleasePairError(
            "release-pair checksum manifest does not match retained bytes"
        )
    manifest = _safe_object(root / PAIR_MANIFEST)
    expected_digests = {
        "backend": backend_registry_digest,
        "frontend": frontend_registry_digest,
    }
    if manifest.get("schema_version") != 1 or (
        manifest.get("version"),
        manifest.get("source_sha"),
        manifest.get("frontend_lock_sha256"),
    ) != (version, source_sha, frontend_lock_sha256):
        raise ReleasePairError(
            "release-pair manifest identity differs from the release"
        )
    images = manifest.get("images")
    if not isinstance(images, dict) or set(images) != set(IMAGE_DIRECTORIES):
        raise ReleasePairError("release-pair manifest image set is invalid")
    for role, package in IMAGE_DIRECTORIES.items():
        image = images.get(role)
        if not isinstance(image, dict) or image != {
            "bundle_manifest_sha256": sha256_file(
                root / role / "release-bundle.sha256"
            ),
            "package": package,
            "receipt_sha256": sha256_file(root / role / "registry-publication.json"),
            "registry_manifest_digest": expected_digests[role],
        }:
            raise ReleasePairError(f"{role} binding differs from retained evidence")
        try:
            verify_recovery_evidence(
                root / role,
                version=version,
                tag=tag,
                source_sha=source_sha,
                registry_digest=expected_digests[role],
            )
        except RecoveryEvidenceError as error:
            raise ReleasePairError(f"{role}: {error}") from error


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--artifacts", required=True, type=Path)
        child.add_argument("--version", required=True)
        child.add_argument("--source-sha", required=True)
    create = subparsers.choices["create"]
    create.add_argument("--frontend-lock", required=True, type=Path)
    verify = subparsers.choices["verify"]
    verify.add_argument("--tag", required=True)
    verify.add_argument("--frontend-lock-sha256", required=True)
    verify.add_argument("--backend-registry-digest", required=True)
    verify.add_argument("--frontend-registry-digest", required=True)
    return parser.parse_args()


def main() -> int:
    """Create or verify the release-pair binding."""
    arguments = _arguments()
    try:
        if arguments.command == "create":
            create_release_pair(
                arguments.artifacts,
                version=arguments.version,
                source_sha=arguments.source_sha,
                frontend_lock=arguments.frontend_lock,
            )
        else:
            verify_release_pair(
                arguments.artifacts,
                version=arguments.version,
                tag=arguments.tag,
                source_sha=arguments.source_sha,
                frontend_lock_sha256=arguments.frontend_lock_sha256,
                backend_registry_digest=arguments.backend_registry_digest,
                frontend_registry_digest=arguments.frontend_registry_digest,
            )
    except (OSError, ReleasePairError) as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
