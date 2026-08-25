"""Verify that a release-image evidence bundle is complete and unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

EXPECTED_FILES = frozenset(
    {
        "image.oci.tar",
        "image-metadata.json",
        "sbom.cdx.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
    }
)
SHA256_LINE = re.compile(r"^([0-9a-f]{64})  ([a-z0-9][a-z0-9.-]*)$")


class SupplyChainVerificationError(ValueError):
    """Raised when release-image evidence is incomplete or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(artifacts: Path) -> dict[str, str]:
    manifest_path = artifacts / "release-bundle.sha256"
    try:
        actual_names = {entry.name for entry in artifacts.iterdir()}
    except OSError as error:
        raise SupplyChainVerificationError(
            "release bundle directory is unavailable"
        ) from error
    if actual_names != EXPECTED_FILES | {manifest_path.name}:
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
    if set(recorded) != EXPECTED_FILES:
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


def verify_bundle(artifacts: Path) -> None:
    """Verify the closed artifact set, checksum manifest, and metadata bindings."""

    recorded = _load_manifest(artifacts)
    _verify_recorded_files(artifacts, recorded)
    metadata = _load_metadata(artifacts)
    metadata_artifacts = metadata.get("artifacts")
    expected_metadata = EXPECTED_FILES - {"image-metadata.json"}
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
    parser.add_argument("--artifacts", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    """Validate one release-image evidence bundle."""

    arguments = _arguments()
    try:
        verify_bundle(arguments.artifacts)
    except SupplyChainVerificationError as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
