"""Fail-closed recovery manifest unit coverage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from markweave.config import StorageProfile
from markweave.recovery_manifest import (
    MANIFEST_SCHEMA,
    RecoveryError,
    RecoveryIdentity,
    RecoveryMember,
    _digest,
    build_manifest,
    load_and_verify_manifest,
    sha256_file,
    write_manifest,
)

pytestmark = pytest.mark.unit


def _identity() -> RecoveryIdentity:
    return RecoveryIdentity("database", "objects", "proof", "source")


def _member() -> RecoveryMember:
    return RecoveryMember("database/payload", 1, hashlib.sha256(b"x").hexdigest())


def _recovery_set(tmp_path: Path) -> Path:
    source = (tmp_path / "set").resolve()
    (source / "database").mkdir(parents=True)
    (source / "database" / "payload").write_bytes(b"x")
    manifest = build_manifest(
        StorageProfile.STANDALONE,
        created_at=datetime.now(UTC),
        identity=_identity(),
        members=(_member(),),
    )
    write_manifest(source, manifest)
    return source


@pytest.mark.parametrize(
    "members",
    (
        (),
        (RecoveryMember("/absolute", 1, "a" * 64),),
        (RecoveryMember("../escape", 1, "a" * 64),),
        (RecoveryMember("database/payload", -1, "a" * 64),),
        (RecoveryMember("database/payload", 1, "invalid"),),
        (
            RecoveryMember("database/payload", 1, "a" * 64),
            RecoveryMember("database/payload", 1, "a" * 64),
        ),
    ),
)
def test_manifest_rejects_invalid_member_lists(
    members: tuple[RecoveryMember, ...],
) -> None:
    with pytest.raises(RecoveryError, match="member"):
        build_manifest(
            StorageProfile.STANDALONE,
            created_at=datetime.now(UTC),
            identity=_identity(),
            members=members,
        )


def test_manifest_rejects_invalid_timestamp_and_identity() -> None:
    with pytest.raises(RecoveryError, match="timestamp"):
        build_manifest(
            StorageProfile.STANDALONE,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            identity=_identity(),
            members=(_member(),),
        )
    with pytest.raises(RecoveryError, match="identity"):
        build_manifest(
            StorageProfile.STANDALONE,
            created_at=datetime.now(UTC),
            identity=RecoveryIdentity("", "objects", "proof", "source"),
            members=(_member(),),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda raw: raw.update(schema="unknown"), "incompatible"),
        (lambda raw: raw.update(backup_id="0" * 64), "identity mismatch"),
        (lambda raw: raw.update(members={}), "invalid"),
        (lambda raw: raw.pop("profile"), "invalid"),
    ),
)
def test_manifest_rejects_invalid_authenticated_payload(
    tmp_path: Path, mutation, message: str
) -> None:
    source = _recovery_set(tmp_path)
    manifest_path = source / "manifest.json"
    raw = json.loads(manifest_path.read_bytes())
    mutation(raw)
    payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    manifest_path.write_bytes(payload)
    (source / "manifest.sha256").write_text(
        hashlib.sha256(payload).hexdigest() + "\n", encoding="ascii"
    )
    with pytest.raises(RecoveryError, match=message):
        load_and_verify_manifest(source)


def test_manifest_rejects_unsafe_sources_and_members(tmp_path: Path) -> None:
    source = _recovery_set(tmp_path)
    with pytest.raises(RecoveryError, match="unsafe"):
        load_and_verify_manifest(Path("relative"))

    payload = source / "database" / "payload"
    payload.unlink()
    payload.symlink_to(source / "manifest.json")
    with pytest.raises(RecoveryError, match="unsafe member"):
        load_and_verify_manifest(source)


def test_manifest_checksum_and_file_hash_fail_closed(tmp_path: Path) -> None:
    source = _recovery_set(tmp_path)
    (source / "manifest.sha256").write_text("0" * 64, encoding="ascii")
    with pytest.raises(RecoveryError, match="integrity"):
        load_and_verify_manifest(source)
    with pytest.raises(RecoveryError, match="could not be read"):
        sha256_file(tmp_path / "missing")
    with pytest.raises(RecoveryError, match="unsafe member"):
        sha256_file(tmp_path)
    assert _digest("a" * 64)
    assert not _digest(42)
    assert MANIFEST_SCHEMA
