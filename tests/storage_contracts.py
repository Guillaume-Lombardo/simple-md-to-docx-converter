"""Shared contracts invoked by both storage-profile integration suites."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from md_converter.auth.models import Role, Session, User
from md_converter.auth.ports import SessionRepository, UserRepository
from md_converter.config import ConfigurationError
from md_converter.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
)


def exercise_auth_repository_contract(
    users: UserRepository, sessions: SessionRepository
) -> None:
    """Verify durable repository semantics needed by the authentication service."""
    admin = users.bootstrap_admin(" Admin ", "admin", "hash:original")
    assert admin.role is Role.ADMIN
    assert users.bootstrap_admin("ADMIN", "admin", "hash:replacement") == admin
    assert users.get_by_id(admin.id) == admin
    assert users.get_by_normalized_username("admin") == admin

    regular = User(uuid4(), "Alice", "alice", "hash:alice", Role.USER)
    users.create(regular)
    with pytest.raises(KeyError):
        users.create(User(uuid4(), "ALICE", "alice", "hash:other", Role.USER))
    assert users.list() == [admin, regular]
    with pytest.raises(ConfigurationError):
        users.bootstrap_admin("Alice", "alice", "hash:replacement")

    upgraded_hash = "hash:" + "upgraded"
    reset_hash = "hash:" + "reset"
    committed = users.commit_verified_login(admin.id, 0, upgraded_hash)
    assert committed is not None
    assert committed.password_hash == upgraded_hash
    changed = users.update_security(admin.id, password_hash=reset_hash)
    assert changed is not None
    assert changed.auth_version == 1
    assert users.commit_verified_login(admin.id, 0, None) is None
    assert users.commit_verified_login(uuid4(), 0, None) is None

    def increment_security_version(_: int) -> None:
        assert users.update_security(admin.id, active=True) is not None

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(increment_security_version, range(4)))
    current = users.get_by_id(admin.id)
    assert current is not None
    assert current.auth_version == 5

    now = datetime(2026, 8, 23, tzinfo=UTC)
    first = Session(
        token_digest="a" * 64,
        csrf_digest="b" * 64,
        user_id=admin.id,
        auth_version=current.auth_version,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )
    second = Session(
        token_digest="c" * 64,
        csrf_digest="d" * 64,
        user_id=admin.id,
        auth_version=current.auth_version,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )
    sessions.create(first)
    sessions.create(second)
    loaded = sessions.get(first.token_digest)
    assert loaded == first
    assert loaded is not None
    loaded.last_seen_at += timedelta(seconds=1)
    sessions.save(loaded)
    assert sessions.get(first.token_digest) == loaded
    sessions.revoke(first.token_digest)
    sessions.revoke(first.token_digest)
    assert sessions.get(first.token_digest) is None
    sessions.revoke_user(admin.id)
    assert sessions.get(second.token_digest) is None


def exercise_object_store_contract(store: ObjectStore) -> None:
    """Verify overwrite, missing-object, and idempotent-delete behavior."""
    key = ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4())
    other = ObjectKey(ObjectScope.RESULT, key.owner_id, uuid4())
    assert key.as_posix().split("/") == [
        "uploads",
        str(key.owner_id),
        str(key.object_id),
    ]
    assert not store.exists(key)
    with pytest.raises(ObjectNotFoundError):
        store.get(key)
    store.put(key, b"first")
    store.put(other, b"other")
    assert store.exists(key)
    assert store.get(key) == b"first"
    store.put(key, b"replacement")
    assert store.get(key) == b"replacement"
    assert store.get(other) == b"other"
    store.delete(key)
    store.delete(key)
    assert not store.exists(key)
