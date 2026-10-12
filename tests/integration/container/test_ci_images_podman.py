"""Real Podman coverage for loading CI image archives only by verified ID."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.container import ci_images
from scripts.container.ci_images import ImageTransferError

pytestmark = [pytest.mark.integration, pytest.mark.requires_podman]

PODMAN = "/usr/bin/podman"


def _podman(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PODMAN, *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _image(tmp_path: Path, name: str, content: str) -> str:
    rootfs = tmp_path / f"rootfs-{content}"
    rootfs.mkdir()
    (rootfs / "identity.txt").write_text(content, encoding="ascii")
    layer = tmp_path / f"{content}.tar"
    with tarfile.open(layer, mode="w") as archive:
        archive.add(rootfs / "identity.txt", arcname="identity.txt")
    _podman("import", str(layer), name)
    return _podman("image", "inspect", name, "--format", "{{.Id}}").stdout.strip()


def test_archive_loads_and_is_tagged_only_after_exact_id_checks(
    tmp_path: Path,
) -> None:
    source = f"localhost/markweave-ci-images-source:{tmp_path.name}"
    target = f"localhost/markweave-ci-images-target:{tmp_path.name}"
    other = f"localhost/markweave-ci-images-other:{tmp_path.name}"
    try:
        image_id = _image(tmp_path, source, "built once")
        other_id = _image(tmp_path, other, "substitute")
        archives = tmp_path / "archives"
        archives.mkdir()
        archive = archives / ci_images.archive_name("frontend")
        _podman("save", "--format", "oci-archive", "--output", str(archive), image_id)

        with pytest.raises(ImageTransferError, match="does not match the expected"):
            ci_images.load(
                "frontend",
                archives,
                {"frontend": f"sha256:{other_id}"},
                names={"frontend": target},
            )
        assert _podman("image", "exists", target, check=False).returncode == 1

        loaded = ci_images.load(
            "frontend",
            archives,
            {"frontend": f"sha256:{image_id}"},
            names={"frontend": target},
        )

        assert loaded == {"frontend": target}
        assert (
            _podman("image", "inspect", target, "--format", "{{.Id}}").stdout.strip()
            == image_id
        )
        with pytest.raises(ImageTransferError, match="refusing to replace"):
            ci_images.load(
                "frontend",
                archives,
                {"frontend": f"sha256:{image_id}"},
                names={"frontend": target},
            )
    finally:
        _podman("rmi", "--force", target, source, other, check=False)
