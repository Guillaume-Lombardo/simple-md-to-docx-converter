"""Tests for retained container evidence recovery validation."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from scripts.container import recover_release_evidence, verify_supply_chain
from scripts.container.integrity import sha256_file
from scripts.container.recover_release_evidence import (
    RecoveryEvidenceError,
    verify_recovery_evidence,
)

pytestmark = pytest.mark.unit

VERSION = "0.3.0"
TAG = "v0.3.0"
SOURCE_SHA = "2" * 40
REGISTRY_DIGEST = "sha256:" + "3" * 64


def _tar_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.mode = 0o600
    archive.addfile(member, io.BytesIO(payload))


def _write_oci_archive(path: Path) -> tuple[str, str]:
    config = json.dumps({"rootfs": {"type": "layers", "diff_ids": []}}).encode()
    config_digest = hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": f"sha256:{config_digest}",
                "size": len(config),
            },
            "layers": [],
        }
    ).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{manifest_digest}",
                    "size": len(manifest),
                }
            ],
        }
    ).encode()
    with tarfile.open(path, mode="w") as archive:
        _tar_bytes(archive, "index.json", index)
        _tar_bytes(archive, f"blobs/sha256/{manifest_digest}", manifest)
        _tar_bytes(archive, f"blobs/sha256/{config_digest}", config)
    return f"sha256:{manifest_digest}", f"sha256:{config_digest}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def recovery_artifacts(tmp_path: Path) -> Path:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    archive_digest, config_digest = _write_oci_archive(artifacts / "image.oci.tar")
    source_names = (
        "image.oci.tar",
        "sbom.cdx.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
    )
    for name in source_names[1:]:
        _write_json(artifacts / name, {"fixture": name})
    source_digests = {name: sha256_file(artifacts / name) for name in source_names}
    _write_json(
        artifacts / "image-metadata.json",
        {
            "artifacts": source_digests,
            "image": {
                "oci_config_digest": config_digest,
                "oci_manifest_digest": archive_digest,
            },
        },
    )
    verify_supply_chain.create_manifest(artifacts)
    _write_json(
        artifacts / "registry-publication.json",
        {
            "oci_archive_manifest_digest": archive_digest,
            "registry_manifest_digest": REGISTRY_DIGEST,
            "source_sha": SOURCE_SHA,
            "version": VERSION,
        },
    )
    return artifacts


def _verify(artifacts: Path) -> None:
    verify_recovery_evidence(
        artifacts,
        version=VERSION,
        tag=TAG,
        source_sha=SOURCE_SHA,
        registry_digest=REGISTRY_DIGEST,
    )


def test_accepts_exact_retained_bundle_and_public_digest(
    recovery_artifacts: Path,
) -> None:
    _verify(recovery_artifacts)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "0.3.1"),
        ("source_sha", "4" * 40),
        ("registry_manifest_digest", "sha256:" + "5" * 64),
        ("oci_archive_manifest_digest", "sha256:" + "6" * 64),
    ],
)
def test_rejects_receipt_identity_mismatch(
    recovery_artifacts: Path, field: str, value: str
) -> None:
    receipt_path = recovery_artifacts / "registry-publication.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    _write_json(receipt_path, receipt)

    with pytest.raises(RecoveryEvidenceError, match="receipt"):
        _verify(recovery_artifacts)


def test_rejects_changed_manifest_bound_file(recovery_artifacts: Path) -> None:
    (recovery_artifacts / "sbom.cdx.json").write_text("changed", encoding="ascii")
    with pytest.raises(RecoveryEvidenceError, match="digest mismatch"):
        _verify(recovery_artifacts)


def test_rejects_extra_and_symlink_entries(
    recovery_artifacts: Path, tmp_path: Path
) -> None:
    extra = recovery_artifacts / "extra"
    extra.write_text("unexpected", encoding="ascii")
    with pytest.raises(RecoveryEvidenceError, match="file set"):
        _verify(recovery_artifacts)
    extra.unlink()

    receipt = recovery_artifacts / "registry-publication.json"
    external = tmp_path / "external-receipt"
    receipt.rename(external)
    receipt.symlink_to(external)
    with pytest.raises(RecoveryEvidenceError, match="unsafe entry"):
        _verify(recovery_artifacts)


def test_rejects_oversized_extracted_artifact(recovery_artifacts: Path, mocker) -> None:
    mocker.patch.object(recover_release_evidence, "MAX_RECOVERY_BYTES", 1)
    with pytest.raises(RecoveryEvidenceError, match="size limit"):
        _verify(recovery_artifacts)


@pytest.mark.parametrize(
    ("version", "tag", "source_sha", "registry_digest"),
    [
        ("0.3.0rc1", TAG, SOURCE_SHA, REGISTRY_DIGEST),
        (VERSION, "wrong", SOURCE_SHA, REGISTRY_DIGEST),
        (VERSION, TAG, "0" * 40, REGISTRY_DIGEST),
        (VERSION, TAG, SOURCE_SHA, "invalid"),
    ],
)
def test_rejects_invalid_requested_identity(
    recovery_artifacts: Path,
    version: str,
    tag: str,
    source_sha: str,
    registry_digest: str,
) -> None:
    with pytest.raises(RecoveryEvidenceError):
        verify_recovery_evidence(
            recovery_artifacts,
            version=version,
            tag=tag,
            source_sha=source_sha,
            registry_digest=registry_digest,
        )
