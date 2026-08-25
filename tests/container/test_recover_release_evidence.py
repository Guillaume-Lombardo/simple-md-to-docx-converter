"""Tests for retained container evidence recovery validation."""

from __future__ import annotations

import hashlib
import io
import json
import sys
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


def _rebind_metadata(artifacts: Path) -> None:
    manifest_path = artifacts / "release-bundle.sha256"
    lines = manifest_path.read_text(encoding="ascii").splitlines()
    metadata_digest = sha256_file(artifacts / "image-metadata.json")
    manifest_path.write_text(
        "\n".join(
            f"{metadata_digest}  image-metadata.json"
            if line.endswith("  image-metadata.json")
            else line
            for line in lines
        )
        + "\n",
        encoding="ascii",
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


@pytest.mark.parametrize("replacement", ("file", "directory", "symlink"))
def test_rejects_unsafe_artifact_directory(tmp_path: Path, replacement: str) -> None:
    artifacts = tmp_path / "artifacts"
    if replacement == "file":
        artifacts.write_text("not a directory", encoding="ascii")
    elif replacement == "directory":
        artifacts.mkdir()
    else:
        target = tmp_path / "target"
        target.mkdir()
        artifacts.symlink_to(target, target_is_directory=True)

    with pytest.raises(RecoveryEvidenceError, match=r"directory is unsafe|file set"):
        _verify(artifacts)


def test_rejects_non_regular_expected_entry(recovery_artifacts: Path) -> None:
    receipt = recovery_artifacts / "registry-publication.json"
    receipt.unlink()
    receipt.mkdir()
    with pytest.raises(RecoveryEvidenceError, match="unsafe entry"):
        _verify(recovery_artifacts)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{", "registry publication receipt is invalid"),
        (b"[]", "registry publication receipt is not an object"),
        (b"", "registry publication receipt size is outside the allowed range"),
    ],
)
def test_rejects_invalid_receipt_json(
    recovery_artifacts: Path, payload: bytes, message: str
) -> None:
    (recovery_artifacts / "registry-publication.json").write_bytes(payload)
    with pytest.raises(RecoveryEvidenceError, match=message):
        _verify(recovery_artifacts)


def test_rejects_oversized_receipt_json(recovery_artifacts: Path, mocker) -> None:
    mocker.patch.object(recover_release_evidence, "MAX_RECEIPT_BYTES", 1)
    with pytest.raises(RecoveryEvidenceError, match="receipt size"):
        _verify(recovery_artifacts)


@pytest.mark.parametrize("payload", (b"{", b"[]"))
def test_rejects_invalid_metadata_json(
    recovery_artifacts: Path, payload: bytes
) -> None:
    (recovery_artifacts / "image-metadata.json").write_bytes(payload)
    _rebind_metadata(recovery_artifacts)
    with pytest.raises(RecoveryEvidenceError, match="image metadata is invalid"):
        _verify(recovery_artifacts)


def test_rejects_oversized_metadata_json(recovery_artifacts: Path, mocker) -> None:
    mocker.patch.object(recover_release_evidence, "MAX_METADATA_BYTES", 1)
    with pytest.raises(RecoveryEvidenceError, match="metadata size"):
        _verify(recovery_artifacts)


def test_rejects_metadata_oci_identity_mismatch(recovery_artifacts: Path) -> None:
    metadata_path = recovery_artifacts / "image-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["image"]["oci_manifest_digest"] = "sha256:" + "9" * 64
    _write_json(metadata_path, metadata)
    _rebind_metadata(recovery_artifacts)
    with pytest.raises(RecoveryEvidenceError, match="does not match the OCI archive"):
        _verify(recovery_artifacts)


def test_rejects_oversized_extracted_artifact(recovery_artifacts: Path, mocker) -> None:
    mocker.patch.object(recover_release_evidence, "MAX_RECOVERY_BYTES", 1)
    with pytest.raises(RecoveryEvidenceError, match="size limit"):
        _verify(recovery_artifacts)


@pytest.mark.parametrize(
    ("version", "tag", "source_sha", "registry_digest"),
    [
        ("not a version", TAG, SOURCE_SHA, REGISTRY_DIGEST),
        ("0.3.0rc1", TAG, SOURCE_SHA, REGISTRY_DIGEST),
        ("0.3.0.dev1", TAG, SOURCE_SHA, REGISTRY_DIGEST),
        ("0.3.0+local", TAG, SOURCE_SHA, REGISTRY_DIGEST),
        ("1!0.3.0", TAG, SOURCE_SHA, REGISTRY_DIGEST),
        (VERSION, "wrong", SOURCE_SHA, REGISTRY_DIGEST),
        (VERSION, TAG, "invalid", REGISTRY_DIGEST),
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


def test_cli_reports_validation_error(
    recovery_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover-release-evidence",
            "--artifacts",
            str(recovery_artifacts),
            "--version",
            VERSION,
            "--tag",
            TAG,
            "--source-sha",
            SOURCE_SHA,
            "--registry-digest",
            "invalid",
        ],
    )
    assert recover_release_evidence.main() == 1
    assert capsys.readouterr().out == "error: public registry digest is invalid\n"


def test_cli_accepts_valid_bundle(
    recovery_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover-release-evidence",
            "--artifacts",
            str(recovery_artifacts),
            "--version",
            VERSION,
            "--tag",
            TAG,
            "--source-sha",
            SOURCE_SHA,
            "--registry-digest",
            REGISTRY_DIGEST,
        ],
    )
    assert recover_release_evidence.main() == 0


def test_cli_reports_os_error(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover-release-evidence",
            "--artifacts",
            "unused",
            "--version",
            VERSION,
            "--tag",
            TAG,
            "--source-sha",
            SOURCE_SHA,
            "--registry-digest",
            REGISTRY_DIGEST,
        ],
    )
    mocker.patch.object(
        recover_release_evidence,
        "verify_recovery_evidence",
        side_effect=OSError("unavailable"),
    )
    assert recover_release_evidence.main() == 1
    assert capsys.readouterr().out == "error: unavailable\n"
