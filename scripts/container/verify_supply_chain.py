"""Verify that a release-image evidence bundle is complete and unchanged."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from scripts.container.integrity import IntegrityError, sha256_file

EXPECTED_FILES = frozenset(
    {
        "image.oci.tar",
        "image-metadata.json",
        "sbom.cdx.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
    }
)
REVERSE_ATTEMPT_FILES = EXPECTED_FILES | {
    "anydoc-cargo.cdx.json",
    "anydoc-cargo-vulnerabilities.json",
}
EVIDENCE_PROFILES = {
    "standard": EXPECTED_FILES,
    "reverse-attempt": REVERSE_ATTEMPT_FILES,
}
SHA256_LINE = re.compile(r"^([0-9a-f]{64})  ([a-z0-9][a-z0-9.-]*)$")


class SupplyChainVerificationError(ValueError):
    """Raised when release-image evidence is incomplete or inconsistent."""


def _sha256(path: Path) -> str:
    try:
        return sha256_file(path)
    except IntegrityError as error:
        raise SupplyChainVerificationError(str(error)) from error


def _load_manifest(
    artifacts: Path,
    expected_manifest_sha256: str,
    *,
    expected_files: frozenset[str] = EXPECTED_FILES,
    allowed_extra_files: frozenset[str] = frozenset(),
) -> dict[str, str]:
    manifest_path = artifacts / "release-bundle.sha256"
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise SupplyChainVerificationError("expected manifest digest is invalid")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SupplyChainVerificationError("checksum manifest is unsafe")
    if _sha256(manifest_path) != expected_manifest_sha256:
        raise SupplyChainVerificationError("checksum manifest trust anchor mismatch")
    try:
        actual_names = {entry.name for entry in artifacts.iterdir()}
    except OSError as error:
        raise SupplyChainVerificationError(
            "release bundle directory is unavailable"
        ) from error
    if actual_names != expected_files | {manifest_path.name} | allowed_extra_files:
        raise SupplyChainVerificationError(
            "release bundle directory does not contain the exact artifact set"
        )
    try:
        lines = manifest_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise SupplyChainVerificationError(
            "release bundle checksum manifest is unavailable"
        ) from error

    recorded: dict[str, str] = {}
    for line in lines:
        match = SHA256_LINE.fullmatch(line)
        if match is None:
            raise SupplyChainVerificationError("checksum manifest is malformed")
        digest, name = match.groups()
        if name in recorded:
            raise SupplyChainVerificationError("checksum manifest contains duplicates")
        recorded[name] = digest
    if set(recorded) != expected_files:
        raise SupplyChainVerificationError(
            "checksum manifest does not name the exact release artifact set"
        )
    return recorded


def _verify_recorded_files(artifacts: Path, recorded: dict[str, str]) -> None:
    for name, expected in recorded.items():
        path = artifacts / name
        if not path.is_file() or path.is_symlink():
            raise SupplyChainVerificationError(f"release artifact is unsafe: {name}")
        if _sha256(path) != expected:
            raise SupplyChainVerificationError(
                f"release artifact digest mismatch: {name}"
            )


def _load_metadata(artifacts: Path) -> dict[str, Any]:
    try:
        value: Any = json.loads(
            (artifacts / "image-metadata.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupplyChainVerificationError("image metadata is invalid") from error
    if not isinstance(value, dict):
        raise SupplyChainVerificationError("image metadata is invalid")
    return value


def create_manifest(
    artifacts: Path, *, expected_files: frozenset[str] = EXPECTED_FILES
) -> str:
    """Create the bundle manifest from producer-recorded streaming digests."""

    metadata = _load_metadata(artifacts)
    source_digests = metadata.get("artifacts")
    expected_sources = expected_files - {"image-metadata.json"}
    if not isinstance(source_digests, dict) or set(source_digests) != expected_sources:
        raise SupplyChainVerificationError(
            "image metadata does not bind the exact source artifact set"
        )
    manifest_path = artifacts / "release-bundle.sha256"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise SupplyChainVerificationError("checksum manifest destination is unsafe")
    recorded = {"image-metadata.json": _sha256(artifacts / "image-metadata.json")}
    for name in expected_sources:
        digest = source_digests[name]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise SupplyChainVerificationError(
                f"image metadata digest is invalid: {name}"
            )
        recorded[name] = digest
    manifest_path.write_text(
        "".join(f"{recorded[name]}  {name}\n" for name in sorted(recorded)),
        encoding="ascii",
    )
    return _sha256(manifest_path)


def verify_bundle(
    artifacts: Path,
    *,
    expected_manifest_sha256: str,
    expected_files: frozenset[str] = EXPECTED_FILES,
    allowed_extra_files: frozenset[str] = frozenset(),
) -> None:
    """Verify the closed artifact set, checksum manifest, and metadata bindings."""

    if artifacts.is_symlink() or not artifacts.is_dir():
        raise SupplyChainVerificationError("release bundle directory is unsafe")
    recorded = _load_manifest(
        artifacts,
        expected_manifest_sha256,
        expected_files=expected_files,
        allowed_extra_files=allowed_extra_files,
    )
    _verify_recorded_files(artifacts, recorded)
    metadata = _load_metadata(artifacts)
    metadata_artifacts = metadata.get("artifacts")
    expected_metadata = expected_files - {"image-metadata.json"}
    if not isinstance(metadata_artifacts, dict) or set(metadata_artifacts) != (
        expected_metadata
    ):
        raise SupplyChainVerificationError(
            "image metadata does not bind the exact source artifact set"
        )
    for name in expected_metadata:
        if metadata_artifacts[name] != recorded[name]:
            raise SupplyChainVerificationError(
                f"image metadata digest mismatch: {name}"
            )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument(
        "--profile", choices=tuple(EVIDENCE_PROFILES), default="standard"
    )
    return parser.parse_args()


def main() -> int:
    """Validate one release-image evidence bundle."""

    arguments = _arguments()
    expected_files = EVIDENCE_PROFILES[arguments.profile]
    try:
        if arguments.action == "create":
            if arguments.expected_manifest_sha256 is not None:
                raise SupplyChainVerificationError(
                    "manifest creation does not accept an expected digest"
                )
            print(create_manifest(arguments.artifacts, expected_files=expected_files))
        else:
            if arguments.expected_manifest_sha256 is None:
                raise SupplyChainVerificationError(
                    "manifest verification requires an expected digest"
                )
            verify_bundle(
                arguments.artifacts,
                expected_manifest_sha256=arguments.expected_manifest_sha256,
                expected_files=expected_files,
            )
    except SupplyChainVerificationError as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
