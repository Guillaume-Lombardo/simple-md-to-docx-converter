"""Streaming integrity and OCI identity helpers for release-image evidence."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any, BinaryIO

OCI_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
MAX_OCI_METADATA_BYTES = 1_048_576


class IntegrityError(ValueError):
    """Raised when a file or OCI archive cannot establish trusted identity."""


def sha256_stream(stream: BinaryIO) -> str:
    """Hash a binary stream without loading it into memory."""

    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a regular file with bounded memory."""

    if path.is_symlink() or not path.is_file():
        raise IntegrityError(f"artifact is not a safe regular file: {path.name}")
    try:
        with path.open("rb") as artifact:
            return sha256_stream(artifact)
    except OSError as error:
        raise IntegrityError(f"artifact cannot be hashed: {path.name}") from error


def _read_json_member(
    archive: tarfile.TarFile, name: str, *, expected_digest: str | None = None
) -> dict[str, Any]:
    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise IntegrityError(f"OCI archive is missing {name}") from error
    if not member.isfile() or member.size > MAX_OCI_METADATA_BYTES:
        raise IntegrityError(f"OCI metadata member is invalid: {name}")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise IntegrityError(f"OCI metadata member is unreadable: {name}")
    payload = extracted.read(MAX_OCI_METADATA_BYTES + 1)
    if expected_digest is not None and hashlib.sha256(payload).hexdigest() != (
        expected_digest
    ):
        raise IntegrityError(f"OCI metadata digest mismatch: {name}")
    try:
        value: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrityError(f"OCI metadata is malformed: {name}") from error
    if not isinstance(value, dict):
        raise IntegrityError(f"OCI metadata is not an object: {name}")
    return value


def _descriptor_digest(value: object, label: str) -> str:
    if not isinstance(value, dict):
        raise IntegrityError(f"OCI {label} descriptor is invalid")
    digest = value.get("digest")
    if not isinstance(digest, str) or (match := OCI_DIGEST.fullmatch(digest)) is None:
        raise IntegrityError(f"OCI {label} digest is invalid")
    return match.group(1)


def oci_identity(path: Path) -> tuple[str, str]:
    """Return the verified OCI manifest and image-config digests."""

    try:
        with tarfile.open(path, mode="r:") as archive:
            index = _read_json_member(archive, "index.json")
            manifests = index.get("manifests")
            if not isinstance(manifests, list) or len(manifests) != 1:
                raise IntegrityError("OCI index must describe exactly one image")
            manifest_hex = _descriptor_digest(manifests[0], "manifest")
            manifest = _read_json_member(
                archive,
                f"blobs/sha256/{manifest_hex}",
                expected_digest=manifest_hex,
            )
            config_hex = _descriptor_digest(manifest.get("config"), "config")
            config_name = f"blobs/sha256/{config_hex}"
            config = _read_json_member(archive, config_name, expected_digest=config_hex)
            if not isinstance(config.get("rootfs"), dict):
                raise IntegrityError("OCI image config has no rootfs identity")
    except (OSError, tarfile.TarError) as error:
        raise IntegrityError(f"OCI archive is invalid: {error}") from error
    return f"sha256:{manifest_hex}", f"sha256:{config_hex}"
