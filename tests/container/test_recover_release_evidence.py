"""Tests for retained container evidence recovery validation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
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


@pytest.mark.unit
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
@pytest.mark.unit
def test_rejects_receipt_identity_mismatch(
    recovery_artifacts: Path, field: str, value: str
) -> None:
    receipt_path = recovery_artifacts / "registry-publication.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    _write_json(receipt_path, receipt)

    with pytest.raises(RecoveryEvidenceError, match="receipt"):
        _verify(recovery_artifacts)


@pytest.mark.unit
def test_rejects_changed_manifest_bound_file(recovery_artifacts: Path) -> None:
    (recovery_artifacts / "sbom.cdx.json").write_text("changed", encoding="ascii")
    with pytest.raises(RecoveryEvidenceError, match="digest mismatch"):
        _verify(recovery_artifacts)


@pytest.mark.unit
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
@pytest.mark.unit
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


@pytest.mark.unit
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
@pytest.mark.unit
def test_rejects_invalid_receipt_json(
    recovery_artifacts: Path, payload: bytes, message: str
) -> None:
    (recovery_artifacts / "registry-publication.json").write_bytes(payload)
    with pytest.raises(RecoveryEvidenceError, match=message):
        _verify(recovery_artifacts)


@pytest.mark.unit
def test_rejects_oversized_receipt_json(recovery_artifacts: Path, mocker) -> None:
    mocker.patch.object(recover_release_evidence, "MAX_RECEIPT_BYTES", 1)
    with pytest.raises(RecoveryEvidenceError, match="receipt size"):
        _verify(recovery_artifacts)


@pytest.mark.parametrize("payload", (b"{", b"[]"))
@pytest.mark.unit
def test_rejects_invalid_metadata_json(
    recovery_artifacts: Path, payload: bytes
) -> None:
    (recovery_artifacts / "image-metadata.json").write_bytes(payload)
    _rebind_metadata(recovery_artifacts)
    with pytest.raises(RecoveryEvidenceError, match="image metadata is invalid"):
        _verify(recovery_artifacts)


@pytest.mark.unit
def test_rejects_oversized_metadata_json(recovery_artifacts: Path, mocker) -> None:
    mocker.patch.object(recover_release_evidence, "MAX_METADATA_BYTES", 1)
    with pytest.raises(RecoveryEvidenceError, match="metadata size"):
        _verify(recovery_artifacts)


@pytest.mark.unit
def test_rejects_metadata_oci_identity_mismatch(recovery_artifacts: Path) -> None:
    metadata_path = recovery_artifacts / "image-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["image"]["oci_manifest_digest"] = "sha256:" + "9" * 64
    _write_json(metadata_path, metadata)
    _rebind_metadata(recovery_artifacts)
    with pytest.raises(RecoveryEvidenceError, match="does not match the OCI archive"):
        _verify(recovery_artifacts)


@pytest.mark.unit
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
@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.fixture
def reverse_recovery_artifacts(recovery_artifacts: Path) -> Path:
    artifacts = recovery_artifacts
    metadata_path = artifacts / "image-metadata.json"
    metadata = json.loads(metadata_path.read_text())
    for name in (
        verify_supply_chain.REVERSE_ATTEMPT_FILES - verify_supply_chain.EXPECTED_FILES
    ):
        _write_json(artifacts / name, {"fixture": name})
        metadata["artifacts"][name] = sha256_file(artifacts / name)
    _write_json(metadata_path, metadata)
    (artifacts / "release-bundle.sha256").unlink()
    verify_supply_chain.create_manifest(
        artifacts, expected_files=verify_supply_chain.REVERSE_ATTEMPT_FILES
    )
    return artifacts


def _verify_reverse(artifacts: Path) -> None:
    verify_recovery_evidence(
        artifacts,
        version=VERSION,
        tag=TAG,
        source_sha=SOURCE_SHA,
        registry_digest=REGISTRY_DIGEST,
        profile="reverse-attempt",
    )


@pytest.mark.unit
def test_reverse_recovery_requires_exact_native_inventory(
    reverse_recovery_artifacts: Path,
) -> None:
    _verify_reverse(reverse_recovery_artifacts)
    with pytest.raises(RecoveryEvidenceError, match="file set"):
        _verify(reverse_recovery_artifacts)


@pytest.mark.parametrize(
    "name", ["anydoc-cargo.cdx.json", "anydoc-cargo-vulnerabilities.json"]
)
@pytest.mark.parametrize("change", ["missing", "changed", "symlink"])
@pytest.mark.unit
def test_reverse_recovery_rejects_missing_changed_or_unsafe_cargo_evidence(
    reverse_recovery_artifacts: Path,
    name: str,
    change: str,
    tmp_path: Path,
) -> None:
    path = reverse_recovery_artifacts / name
    if change == "missing":
        path.unlink()
    elif change == "changed":
        path.write_text("changed")
    else:
        destination = tmp_path / "external"
        path.rename(destination)
        path.symlink_to(destination)
    with pytest.raises(
        RecoveryEvidenceError, match=r"file set|digest mismatch|unsafe entry"
    ):
        _verify_reverse(reverse_recovery_artifacts)


@pytest.mark.unit
def test_standard_bundle_cannot_impersonate_reverse_profile(
    recovery_artifacts: Path,
) -> None:
    with pytest.raises(RecoveryEvidenceError, match="file set"):
        _verify_reverse(recovery_artifacts)
    with pytest.raises(RecoveryEvidenceError, match="profile is invalid"):
        verify_recovery_evidence(
            recovery_artifacts,
            version=VERSION,
            tag=TAG,
            source_sha=SOURCE_SHA,
            registry_digest=REGISTRY_DIGEST,
            profile="unknown",
        )


_PUBLISHER_TOOLS = r"""import json, os, sys, tarfile
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["REGISTRY_STATE"])
state = json.loads(state_path.read_text())
def save():
    state_path.write_text(json.dumps(state))
if name == "uv":
    os.execv(sys.executable, [sys.executable, *args[2:]])
elif name == "skopeo":
    if args[0] == "login":
        Path(args[args.index("--authfile") + 1]).write_text("{}")
    elif args[-2].startswith("oci-archive:"):
        with tarfile.open(args[-2].removeprefix("oci-archive:")) as archive:
            index = json.load(archive.extractfile("index.json"))
            digest = index["manifests"][0]["digest"].split(":")[1]
            payload = archive.extractfile("blobs/sha256/" + digest).read()
        Path(args[-1].removeprefix("dir:"), "manifest.json").write_bytes(payload)
    else:
        target = args[-1].removeprefix("docker://ghcr.io/")
        role, tag = target.rsplit(":", 1)
        state["copies"].append(target)
        state["events"].append("copy:" + target)
        save()
        if os.environ.get("FAIL_PACKAGE") == role:
            sys.exit(1)
        import hashlib
        digest = "sha256:" + hashlib.sha256(Path(args[-2].removeprefix("dir:"), "manifest.json").read_bytes()).hexdigest()
        state["tags"][target] = digest
        save()
        if os.environ.get("FAIL_AFTER_STORE"):
            sys.exit(1)
elif name == "curl":
    url = args[-1]
    if "/token?" in url:
        print('{"token":"fixture-token"}')
    else:
        package, tag = url.split("/v2/", 1)[1].split("/manifests/")
        key = package + ":" + tag
        state["inspections"].append(key)
        state["events"].append("inspect:" + key)
        save()
        digest = state["tags"].get(key)
        Path(args[args.index("--dump-header") + 1]).write_text("Docker-Content-Digest: " + digest + "\r\n" if digest else "")
        print("200" if digest else "404", end="")
"""


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [
        "fresh",
        "conflict",
        "partial",
        "stored-despite-error",
        "tampered",
        "legacy",
        "legacy-missing",
        "legacy-conflict",
        "missing-third",
    ],
)
def test_three_image_publisher_preflight_and_recovery(  # noqa: PLR0912, PLR0915 - exercise publication and recovery scenarios
    reverse_recovery_artifacts: Path,
    tmp_path: Path,
    scenario: str,
) -> None:

    # The reverse fixture extends the standard fixture; derive both exact profiles.
    stage = tmp_path / "stage"
    stage.mkdir()
    for role in ("backend", "frontend", "reverse_attempt"):
        destination = stage / role
        shutil.copytree(reverse_recovery_artifacts, destination)
        (destination / "registry-publication.json").unlink()
        if role != "reverse_attempt":
            metadata = json.loads((destination / "image-metadata.json").read_text())
            for name in (
                verify_supply_chain.REVERSE_ATTEMPT_FILES
                - verify_supply_chain.EXPECTED_FILES
            ):
                (destination / name).unlink()
                del metadata["artifacts"][name]
            _write_json(destination / "image-metadata.json", metadata)
            (destination / "release-bundle.sha256").unlink()
            verify_supply_chain.create_manifest(destination)
    legacy = scenario.startswith("legacy")
    if legacy or scenario == "missing-third":
        shutil.rmtree(stage / "reverse_attempt")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("uv", "skopeo", "curl"):
        tool = binaries / name
        tool.write_text(f"#!{sys.executable}\n" + _PUBLISHER_TOOLS)
        tool.chmod(0o700)
    state_path = tmp_path / "registry.json"
    state = {"copies": [], "tags": {}, "inspections": [], "events": []}
    if scenario == "conflict":
        state["tags"][f"guillaume-lombardo/md-converter-reverse-attempt:{VERSION}"] = (
            "sha256:" + "f" * 64
        )
    if legacy:
        digest = json.loads((stage / "backend/image-metadata.json").read_text())[
            "image"
        ]["oci_manifest_digest"]
        for package in ("md-converter", "md-converter-web"):
            for tag in (VERSION, f"source-{SOURCE_SHA}"):
                state["tags"][f"guillaume-lombardo/{package}:{tag}"] = digest
        if scenario == "legacy-missing":
            del state["tags"][f"guillaume-lombardo/md-converter-web:{VERSION}"]
        elif scenario == "legacy-conflict":
            state["tags"][f"guillaume-lombardo/md-converter-web:{VERSION}"] = (
                "sha256:" + "f" * 64
            )
    _write_json(state_path, state)
    lock = tmp_path / "pnpm-lock.yaml"
    lock.write_text("lockfileVersion: '9.0'\n")
    env = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "GHCR_TOKEN": "fixture-token",
        "GITHUB_ACTOR": "fixture-actor",
        "RUNNER_TEMP": str(tmp_path),
        "REGISTRY_STATE": str(state_path),
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
    }
    if scenario == "partial":
        env["FAIL_PACKAGE"] = "guillaume-lombardo/md-converter-web"
    if scenario == "stored-despite-error":
        env["FAIL_AFTER_STORE"] = "1"
    if scenario == "tampered":
        (stage / "reverse_attempt/anydoc-cargo.cdx.json").write_text("tampered")
    command = [
        "bash",
        "scripts/container/publish-release-pair.sh",
        str(stage),
        VERSION,
        SOURCE_SHA,
        str(lock),
    ]
    if legacy:
        source = tmp_path / "release-source.py"
        source.write_text(
            "IMAGE_DIRECTORIES = "
            + repr(
                {
                    "backend": "ghcr.io/guillaume-lombardo/md-converter",
                    "frontend": "ghcr.io/guillaume-lombardo/md-converter-web",
                }
            )
        )
        selection = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.container.release_pair",
                "source-schema",
                "--source-module",
                str(source),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert selection.stdout.strip() == "1"
        command.append("--recover-legacy-pair")
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, check=False
    )
    state = json.loads(state_path.read_text())
    if scenario in (
        "conflict",
        "tampered",
        "legacy-missing",
        "legacy-conflict",
        "missing-third",
    ):
        assert result.returncode != 0
        assert state["copies"] == []
        assert not (tmp_path / "outputs").exists()
        return
    if scenario == "partial":
        assert result.returncode != 0
        assert len(state["tags"]) == 2  # Both backend tags survived the interruption.
        assert not (stage / "release-images.json").exists()
        assert not (tmp_path / "outputs").exists()
        # Recovery uses the retained prepublication bundle, not partial receipts.
        for receipt in stage.glob("*/registry-publication.json"):
            receipt.unlink()
        del env["FAIL_PACKAGE"]
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, check=False
        )
        state = json.loads(state_path.read_text())
        assert state["copies"].count(f"guillaume-lombardo/md-converter:{VERSION}") == 1
    assert result.returncode == 0, result.stderr + result.stdout
    if legacy:
        assert len(state["tags"]) == 4
        assert state["copies"] == []
        manifest = json.loads((stage / "release-images.json").read_text())
        assert manifest["schema_version"] == 1
        assert set(manifest["images"]) == {"backend", "frontend"}
        assert "reverse-attempt-digest=\n" in (tmp_path / "outputs").read_text()
        return
    assert len(state["tags"]) == 6
    first_copy = next(
        index
        for index, event in enumerate(state["events"])
        if event.startswith("copy:")
    )
    assert len(set(state["events"][:first_copy])) == 6
    manifest = json.loads((stage / "release-images.json").read_text())
    assert manifest["schema_version"] == 2
    assert set(manifest["images"]) == {"backend", "frontend", "reverse_attempt"}
    assert "reverse-attempt-digest=sha256:" in (tmp_path / "outputs").read_text()
