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
    "reverse_attempt": "sha256:" + "d" * 64,
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
        reverse_attempt_registry_digest=DIGESTS["reverse_attempt"],
    )


def test_binds_both_receipts_and_frontend_lock(pair: tuple[Path, Path]) -> None:
    root, lock = pair
    _verify(root, lock)
    manifest = json.loads((root / release_pair.PAIR_MANIFEST).read_text())
    assert manifest["version"] == VERSION
    assert manifest["source_sha"] == SOURCE_SHA
    assert set(manifest["images"]) == {"backend", "frontend", "reverse_attempt"}
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
            reverse_attempt_registry_digest=DIGESTS["reverse_attempt"],
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


def _rebind_pair(root: Path) -> None:
    (root / release_pair.PAIR_CHECKSUMS).write_text(
        "".join(
            f"{release_pair.sha256_file(path)}  {path.relative_to(root).as_posix()}\n"
            for path in release_pair._relative_files(root)
        ),
        encoding="ascii",
    )


def test_historical_pair_remains_verifiable(pair: tuple[Path, Path]) -> None:
    root, lock = pair
    reverse = root / "reverse_attempt"
    for path in reverse.iterdir():
        path.unlink()
    reverse.rmdir()
    manifest_path = root / release_pair.PAIR_MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 1
    del manifest["images"]["reverse_attempt"]
    manifest_path.write_text(json.dumps(manifest))
    _rebind_pair(root)
    verify_release_pair(
        root,
        version=VERSION,
        tag=TAG,
        source_sha=SOURCE_SHA,
        frontend_lock_sha256=release_pair.sha256_file(lock),
        backend_registry_digest=DIGESTS["backend"],
        frontend_registry_digest=DIGESTS["frontend"],
    )
    with pytest.raises(ReleasePairError, match="historical pair"):
        _verify(root, lock)
    with pytest.raises(ReleasePairError, match="reverse_attempt evidence directory"):
        create_release_pair(
            root, version=VERSION, source_sha=SOURCE_SHA, frontend_lock=lock
        )


def test_current_release_requires_reverse_digest(pair: tuple[Path, Path]) -> None:
    root, lock = pair
    with pytest.raises(ReleasePairError, match="requires a reverse-attempt digest"):
        verify_release_pair(
            root,
            version=VERSION,
            tag=TAG,
            source_sha=SOURCE_SHA,
            frontend_lock_sha256=release_pair.sha256_file(lock),
            backend_registry_digest=DIGESTS["backend"],
            frontend_registry_digest=DIGESTS["frontend"],
        )


def test_reverse_receipt_uses_native_evidence_profile(
    pair: tuple[Path, Path], mocker
) -> None:
    root, lock = pair
    verifier = mocker.patch.object(release_pair, "verify_recovery_evidence")
    _verify(root, lock)
    reverse_call = next(
        call
        for call in verifier.call_args_list
        if call.args[0].name == "reverse_attempt"
    )
    assert reverse_call.kwargs["profile"] == "reverse-attempt"
    assert reverse_call.kwargs["registry_digest"] == DIGESTS["reverse_attempt"]
    verifier.side_effect = release_pair.RecoveryEvidenceError(
        "invalid native inventory"
    )
    with pytest.raises(ReleasePairError, match="invalid native inventory"):
        _verify(root, lock)


@pytest.mark.parametrize("schema", [0, 3, True, "2"])
def test_unknown_manifest_schema_fails_closed(
    pair: tuple[Path, Path], schema: object
) -> None:
    root, lock = pair
    path = root / release_pair.PAIR_MANIFEST
    manifest = json.loads(path.read_text())
    manifest["schema_version"] = schema
    path.write_text(json.dumps(manifest))
    _rebind_pair(root)
    with pytest.raises(ReleasePairError, match="schema is invalid"):
        _verify(root, lock)


@pytest.mark.parametrize("schema", [1, 2])
def test_schema_selection_uses_exact_trusted_source_map(
    tmp_path: Path, schema: int
) -> None:
    roles = dict(IMAGE_DIRECTORIES)
    if schema == 1:
        del roles["reverse_attempt"]
    source = tmp_path / "source.py"
    source.write_text("IMAGE_DIRECTORIES = " + repr(roles))
    assert release_pair.source_schema(source) == schema
    roles["backend"] = "ghcr.io/untrusted/backend"
    source.write_text("IMAGE_DIRECTORIES = " + repr(roles))
    with pytest.raises(ReleasePairError, match="unsupported"):
        release_pair.source_schema(source)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "IMAGE_DIRECTORIES = dynamic()",
        "IMAGE_DIRECTORIES = {",
        "IMAGE_DIRECTORIES = {}\nIMAGE_DIRECTORIES = {}",
    ],
)
def test_schema_selection_rejects_ambiguous_or_executable_source(
    tmp_path: Path, payload: str
) -> None:
    source = tmp_path / "source.py"
    source.write_text(payload)
    with pytest.raises(ReleasePairError, match="invalid"):
        release_pair.source_schema(source)
