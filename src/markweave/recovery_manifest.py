"""Canonical, content-addressed recovery-set manifests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from markweave.config import StorageProfile

MANIFEST_SCHEMA = "markweave-recovery-set-v1"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_IDENTITY_CHARACTERS = 1024
MAX_MEMBERS = 1_000_000
SHA256_CHARACTERS = 64


class RecoveryError(RuntimeError):
    """Safe operational recovery failure."""


@dataclass(frozen=True, slots=True)
class RecoveryMember:
    """One regular recovery-set payload member."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    """Identity binding for one complete profile-specific recovery point."""

    schema: str
    backup_id: str
    profile: str
    created_at: str
    database_identity: str
    object_identity: str
    consistency_proof: str
    source_identity: str
    members: tuple[RecoveryMember, ...]

    def payload(self, *, include_id: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_id:
            value.pop("backup_id")
        return value


@dataclass(frozen=True, slots=True)
class RecoveryIdentity:
    """Provider identities and consistency evidence bound by one manifest."""

    database: str
    objects: str
    consistency_proof: str
    source: str


def sha256_file(path: Path) -> tuple[int, str]:
    """Hash a regular file without following a final symlink."""

    descriptor: int | None = None
    try:
        if path.is_symlink():
            raise RecoveryError("Recovery set contains an unsafe member")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RecoveryError("Recovery set contains an unsafe member")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while block := source.read(1024 * 1024):
                size += len(block)
                digest.update(block)
        return size, digest.hexdigest()
    except OSError:
        raise RecoveryError("Recovery set could not be read") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def canonical_json(value: Any) -> bytes:
    """Serialize identity-bearing JSON deterministically."""

    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_manifest(
    profile: StorageProfile,
    *,
    created_at: datetime,
    identity: RecoveryIdentity,
    members: tuple[RecoveryMember, ...],
) -> RecoveryManifest:
    """Build a canonical manifest whose identifier covers every recovery identity."""

    if created_at.tzinfo is None:
        raise RecoveryError("Recovery timestamp is invalid")
    strings = (
        identity.database,
        identity.objects,
        identity.consistency_proof,
        identity.source,
    )
    if any(
        not value.strip() or len(value) > MAX_IDENTITY_CHARACTERS for value in strings
    ):
        raise RecoveryError("Recovery identity is invalid")
    ordered = tuple(sorted(members, key=lambda member: member.path))
    _validate_members(ordered)
    base = {
        "schema": MANIFEST_SCHEMA,
        "profile": profile.value,
        "created_at": created_at.astimezone(UTC).isoformat(),
        "database_identity": identity.database,
        "object_identity": identity.objects,
        "consistency_proof": identity.consistency_proof,
        "source_identity": identity.source,
        "members": [asdict(member) for member in ordered],
    }
    backup_id = hashlib.sha256(canonical_json(base)).hexdigest()
    return RecoveryManifest(
        schema=MANIFEST_SCHEMA,
        backup_id=backup_id,
        profile=profile.value,
        created_at=base["created_at"],
        database_identity=identity.database,
        object_identity=identity.objects,
        consistency_proof=identity.consistency_proof,
        source_identity=identity.source,
        members=ordered,
    )


def write_manifest(directory: Path, manifest: RecoveryManifest) -> None:
    """Durably write the manifest and its detached checksum."""

    payload = canonical_json(manifest.payload()) + b"\n"
    checksum = hashlib.sha256(payload).hexdigest().encode("ascii") + b"\n"
    _exclusive_write(directory / "manifest.json", payload)
    _exclusive_write(directory / "manifest.sha256", checksum)
    _sync_directory(directory)


def load_and_verify_manifest(directory: Path) -> RecoveryManifest:
    """Read and authenticate a complete recovery set before any restore mutation."""

    try:
        if (
            not directory.is_absolute()
            or directory.resolve() != directory
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            raise RecoveryError("Recovery source is unsafe")
        manifest_path = directory / "manifest.json"
        checksum_path = directory / "manifest.sha256"
        if manifest_path.is_symlink() or checksum_path.is_symlink():
            raise RecoveryError("Recovery set contains an unsafe member")
        payload = manifest_path.read_bytes()
        if not payload or len(payload) > MAX_MANIFEST_BYTES:
            raise RecoveryError("Recovery manifest is invalid")
        expected_checksum = checksum_path.read_text(encoding="ascii").strip()
        if (
            not _digest(expected_checksum)
            or hashlib.sha256(payload).hexdigest() != expected_checksum
        ):
            raise RecoveryError("Recovery manifest integrity check failed")
        raw = json.loads(payload)
        if not isinstance(raw, dict) or set(raw) != {
            "schema",
            "backup_id",
            "profile",
            "created_at",
            "database_identity",
            "object_identity",
            "consistency_proof",
            "source_identity",
            "members",
        }:
            raise RecoveryError("Recovery manifest is invalid")
        members_raw = raw["members"]
        if not isinstance(members_raw, list):
            raise RecoveryError("Recovery manifest is invalid")
        members = tuple(RecoveryMember(**item) for item in members_raw)
        manifest = RecoveryManifest(
            schema=raw["schema"],
            backup_id=raw["backup_id"],
            profile=raw["profile"],
            created_at=raw["created_at"],
            database_identity=raw["database_identity"],
            object_identity=raw["object_identity"],
            consistency_proof=raw["consistency_proof"],
            source_identity=raw["source_identity"],
            members=members,
        )
        if manifest.schema != MANIFEST_SCHEMA or manifest.profile not in {
            item.value for item in StorageProfile
        }:
            raise RecoveryError("Recovery manifest is incompatible")
        rebuilt = build_manifest(
            StorageProfile(manifest.profile),
            created_at=datetime.fromisoformat(manifest.created_at),
            identity=RecoveryIdentity(
                manifest.database_identity,
                manifest.object_identity,
                manifest.consistency_proof,
                manifest.source_identity,
            ),
            members=manifest.members,
        )
        if rebuilt.backup_id != manifest.backup_id:
            raise RecoveryError("Recovery manifest identity mismatch")
        _verify_exact_members(directory, manifest.members)
        return manifest
    except KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError, OSError:
        raise RecoveryError("Recovery manifest is invalid") from None


def _verify_exact_members(directory: Path, members: tuple[RecoveryMember, ...]) -> None:
    expected = {
        "manifest.json",
        "manifest.sha256",
        *(member.path for member in members),
    }
    observed: set[str] = set()
    for root, directories, files in os.walk(directory, followlinks=False):
        root_path = Path(root)
        for name in directories:
            if (root_path / name).is_symlink():
                raise RecoveryError("Recovery set contains an unsafe member")
        for name in files:
            path = root_path / name
            relative = path.relative_to(directory).as_posix()
            observed.add(relative)
    if observed != expected:
        raise RecoveryError("Recovery set is incomplete or contains unexpected files")
    for member in members:
        size, digest = sha256_file(directory / member.path)
        if size != member.size or digest != member.sha256:
            raise RecoveryError("Recovery set member integrity check failed")


def _validate_members(members: tuple[RecoveryMember, ...]) -> None:
    if not members or len(members) > MAX_MEMBERS:
        raise RecoveryError("Recovery member list is invalid")
    paths: set[str] = set()
    for member in members:
        path = PurePosixPath(member.path)
        if (
            path.is_absolute()
            or not member.path
            or ".." in path.parts
            or "." in path.parts
            or member.path in paths
            or member.size < 0
            or not _digest(member.sha256)
        ):
            raise RecoveryError("Recovery member is invalid")
        paths.add(member.path)


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_CHARACTERS
        and all(character in "0123456789abcdef" for character in value)
    )


def _exclusive_write(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise RecoveryError("Recovery manifest could not be retained") from None


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
