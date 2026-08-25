"""Validate retained container evidence before a provenance-only recovery."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from scripts.container.integrity import IntegrityError, oci_identity, sha256_file
from scripts.container.verify_supply_chain import (
    EXPECTED_FILES,
    SupplyChainVerificationError,
    verify_bundle,
)

PUBLICATION_RECEIPT = "registry-publication.json"
RECOVERY_FILES = EXPECTED_FILES | {"release-bundle.sha256", PUBLICATION_RECEIPT}
FULL_SHA = re.compile(r"[0-9a-f]{40}")
OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_RECEIPT_BYTES = 16_384
MAX_METADATA_BYTES = 1_048_576
MAX_RECOVERY_BYTES = 4 * 1024 * 1024 * 1024


class RecoveryEvidenceError(ValueError):
    """The retained artifact does not prove the requested release identity."""


def _safe_json(path: Path, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecoveryEvidenceError(f"{label} is not a safe regular file")
    try:
        size = path.stat().st_size
        if not 0 < size <= maximum_bytes:
            raise RecoveryEvidenceError(f"{label} size is outside the allowed range")
        value: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecoveryEvidenceError(f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise RecoveryEvidenceError(f"{label} is not an object")
    return value


def _validate_version(version: str, tag: str) -> None:
    try:
        parsed = Version(version)
    except InvalidVersion as error:
        raise RecoveryEvidenceError("release version is invalid") from error
    if (
        version != str(parsed)
        or parsed.is_prerelease
        or parsed.is_devrelease
        or parsed.local is not None
        or parsed.epoch != 0
    ):
        raise RecoveryEvidenceError("release version is not canonical and final")
    if tag != f"v{version}":
        raise RecoveryEvidenceError("release tag does not match the version")


def verify_recovery_evidence(
    artifacts: Path,
    *,
    version: str,
    tag: str,
    source_sha: str,
    registry_digest: str,
) -> None:
    """Verify the exact retained bundle and its public-registry relationship."""
    _validate_version(version, tag)
    if FULL_SHA.fullmatch(source_sha) is None or source_sha == "0" * 40:
        raise RecoveryEvidenceError("release source SHA is invalid")
    if OCI_DIGEST.fullmatch(registry_digest) is None:
        raise RecoveryEvidenceError("public registry digest is invalid")
    if artifacts.is_symlink() or not artifacts.is_dir():
        raise RecoveryEvidenceError("recovery artifact directory is unsafe")
    entries = tuple(artifacts.iterdir())
    if {entry.name for entry in entries} != RECOVERY_FILES:
        raise RecoveryEvidenceError("recovery artifact file set is not exact")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise RecoveryEvidenceError("recovery artifact contains an unsafe entry")
    if sum(entry.stat().st_size for entry in entries) > MAX_RECOVERY_BYTES:
        raise RecoveryEvidenceError("recovery artifact exceeds the size limit")

    manifest_path = artifacts / "release-bundle.sha256"
    try:
        manifest_digest = sha256_file(manifest_path)
        verify_bundle(
            artifacts,
            expected_manifest_sha256=manifest_digest,
            allowed_extra_files=frozenset({PUBLICATION_RECEIPT}),
        )
        archive_manifest, archive_config = oci_identity(artifacts / "image.oci.tar")
    except (IntegrityError, SupplyChainVerificationError) as error:
        raise RecoveryEvidenceError(str(error)) from error

    metadata = _safe_json(
        artifacts / "image-metadata.json",
        label="image metadata",
        maximum_bytes=MAX_METADATA_BYTES,
    )
    image = metadata.get("image")
    if not isinstance(image, dict) or (
        image.get("oci_manifest_digest"),
        image.get("oci_config_digest"),
    ) != (archive_manifest, archive_config):
        raise RecoveryEvidenceError("image metadata does not match the OCI archive")

    receipt = _safe_json(
        artifacts / PUBLICATION_RECEIPT,
        label="registry publication receipt",
        maximum_bytes=MAX_RECEIPT_BYTES,
    )
    expected_receipt = {
        "oci_archive_manifest_digest": archive_manifest,
        "registry_manifest_digest": registry_digest,
        "source_sha": source_sha,
        "version": version,
    }
    if receipt != expected_receipt:
        raise RecoveryEvidenceError(
            "registry publication receipt does not match the release identity"
        )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--registry-digest", required=True)
    return parser.parse_args()


def main() -> int:
    """Validate retained evidence for the manual workflow boundary."""
    arguments = _arguments()
    try:
        verify_recovery_evidence(
            arguments.artifacts,
            version=arguments.version,
            tag=arguments.tag,
            source_sha=arguments.source_sha,
            registry_digest=arguments.registry_digest,
        )
    except (OSError, RecoveryEvidenceError) as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
