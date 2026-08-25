"""Real Podman coverage for the registry serialization identity."""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.container.integrity import oci_identity

pytestmark = pytest.mark.integration

PODMAN = "/usr/bin/podman"


def _podman(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PODMAN, *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_registry_stage_digest_matches_exact_manifest_bytes(tmp_path: Path) -> None:
    """The dir transport, not the OCI archive, defines the registry digest."""
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "identity.txt").write_text("registry transport\n", encoding="ascii")
    layer = tmp_path / "layer.tar"
    with tarfile.open(layer, mode="w") as archive:
        archive.add(rootfs / "identity.txt", arcname="identity.txt")

    image = f"localhost/markweave-registry-transport:{tmp_path.name}"
    try:
        _podman("import", str(layer), image)
        oci_archive = tmp_path / "image.oci.tar"
        _podman("save", "--format", "oci-archive", "--output", str(oci_archive), image)

        registry_stage = tmp_path / "registry-stage"
        registry_stage.mkdir(mode=0o700)
        digest_file = tmp_path / "registry.digest"
        _podman(
            "push",
            "--format",
            "oci",
            "--digestfile",
            str(digest_file),
            image,
            f"dir:{registry_stage}",
        )

        staged_digest = digest_file.read_text(encoding="ascii").strip()
        manifest_digest = (
            "sha256:"
            + hashlib.sha256(
                (registry_stage / "manifest.json").read_bytes()
            ).hexdigest()
        )
        archive_digest, _ = oci_identity(oci_archive)

        assert staged_digest == manifest_digest
        assert archive_digest != staged_digest
    finally:
        _podman("image", "rm", "--force", image, check=False)
