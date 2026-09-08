"""Tests for the two-image release evidence binding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.container import release_pair
from scripts.container.release_pair import (
    IMAGE_DIRECTORIES,
    ReleasePairError,
    create_release_pair,
    verify_release_pair,
)

pytestmark = pytest.mark.unit

VERSION = "0.6.0"
TAG = "v0.6.0"
SOURCE_SHA = "a" * 40
DIGESTS = {
    "backend": "sha256:" + "b" * 64,
    "frontend": "sha256:" + "c" * 64,
}


@pytest.fixture
def pair(tmp_path: Path, mocker) -> tuple[Path, Path]:
    root = tmp_path / "release"
    root.mkdir()
    for role in IMAGE_DIRECTORIES:
        directory = root / role
        directory.mkdir()
        (directory / "release-bundle.sha256").write_text(
            f"{'d' * 64}  image.oci.tar\n", encoding="ascii"
        )
        (directory / "registry-publication.json").write_text(
            json.dumps(
                {
                    "oci_archive_manifest_digest": "sha256:" + "e" * 64,
                    "registry_manifest_digest": DIGESTS[role],
                    "source_sha": SOURCE_SHA,
                    "version": VERSION,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    mocker.patch.object(release_pair, "verify_recovery_evidence")
    create_release_pair(
        root, version=VERSION, source_sha=SOURCE_SHA, frontend_lock=lock
    )
    return root, lock


def _verify(root: Path, lock: Path) -> None:
    verify_release_pair(
        root,
        version=VERSION,
        tag=TAG,
        source_sha=SOURCE_SHA,
        frontend_lock_sha256=release_pair.sha256_file(lock),
        backend_registry_digest=DIGESTS["backend"],
        frontend_registry_digest=DIGESTS["frontend"],
    )


def test_binds_both_receipts_and_frontend_lock(pair: tuple[Path, Path]) -> None:
    root, lock = pair
    _verify(root, lock)
    manifest = json.loads((root / release_pair.PAIR_MANIFEST).read_text())
    assert manifest["version"] == VERSION
    assert manifest["source_sha"] == SOURCE_SHA
    assert set(manifest["images"]) == {"backend", "frontend"}
    assert manifest["images"]["frontend"]["package"].endswith("md-converter-web")


def test_rejects_cross_release_pairing(pair: tuple[Path, Path]) -> None:
    root, lock = pair
    with pytest.raises(ReleasePairError, match="frontend binding"):
        verify_release_pair(
            root,
            version=VERSION,
            tag=TAG,
            source_sha=SOURCE_SHA,
            frontend_lock_sha256=release_pair.sha256_file(lock),
            backend_registry_digest=DIGESTS["backend"],
            frontend_registry_digest="sha256:" + "f" * 64,
        )


def test_rejects_any_changed_retained_byte(pair: tuple[Path, Path]) -> None:
    root, lock = pair
    receipt = root / "backend" / "registry-publication.json"
    receipt.write_text(receipt.read_text() + " ", encoding="utf-8")
    with pytest.raises(ReleasePairError, match="checksum manifest"):
        _verify(root, lock)


def test_rejects_extra_or_unsafe_entries(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    root, lock = pair
    (root / "extra").mkdir()
    with pytest.raises(ReleasePairError, match="unexpected entry"):
        _verify(root, lock)
    (root / "extra").rmdir()
    receipt = root / "frontend" / "registry-publication.json"
    target = tmp_path / "external"
    receipt.rename(target)
    receipt.symlink_to(target)
    with pytest.raises(ReleasePairError, match="unsafe entry"):
        _verify(root, lock)


def test_create_rejects_mismatched_receipt_identity(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir()
    for role in IMAGE_DIRECTORIES:
        directory = root / role
        directory.mkdir()
        (directory / "release-bundle.sha256").write_text("manifest\n")
        (directory / "registry-publication.json").write_text(
            json.dumps(
                {
                    "registry_manifest_digest": DIGESTS[role],
                    "source_sha": "f" * 40,
                    "version": VERSION,
                }
            )
        )
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}")
    with pytest.raises(ReleasePairError, match="receipt identity"):
        create_release_pair(
            root, version=VERSION, source_sha=SOURCE_SHA, frontend_lock=lock
        )
