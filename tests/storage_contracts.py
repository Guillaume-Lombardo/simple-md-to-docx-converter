"""Shared contracts invoked by both storage-profile integration suites."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from markweave.auth.errors import AuthenticationError
from markweave.auth.models import Role, Session, User
from markweave.auth.ports import SessionRepository, UserRepository
from markweave.config import ConfigurationError
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
)
from markweave.templates.errors import TemplateUnavailableError
from markweave.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplateSearch,
    TemplateStatus,
)
from markweave.templates.ports import (
    TemplateCatalogRepository,
    TemplateSelectionRepository,
)
from markweave.templates.service import TemplateOperation, TemplateService


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


def exercise_template_repository_contract(
    users: UserRepository,
    catalog: TemplateCatalogRepository,
    selections: TemplateSelectionRepository,
) -> None:
    """Verify cross-profile template visibility, search, and selection parity."""
    admin = User(uuid4(), "Admin", f"admin-{uuid4()}", "hash:admin", Role.ADMIN)
    alice = User(uuid4(), "Alice", f"alice-{uuid4()}", "hash:alice", Role.USER)
    bob = User(uuid4(), "Bob", f"bob-{uuid4()}", "hash:bob", Role.USER)
    for user in (admin, alice, bob):
        users.create(user)

    alpha = TemplateIdentity(
        uuid4(), alice.id, "Alpha", "First shared template", TemplateStatus.ACTIVE
    )
    finance = TemplateIdentity(
        uuid4(),
        alice.id,
        "\uff26\uff49\uff4e\uff41\uff4e\uff43\uff45 %_",
        "Quarterly Résumé",
        TemplateStatus.ACTIVE,
    )
    general = TemplateIdentity(
        uuid4(), bob.id, "General", "Company default", TemplateStatus.ACTIVE
    )
    archived = TemplateIdentity(
        uuid4(), alice.id, "Legacy", "Retired template", TemplateStatus.ARCHIVED
    )
    for template in (general, archived, finance, alpha):
        catalog.add(template)

    bob_visible = catalog.search(
        TemplateSearch(limit=10), viewer_id=bob.id, viewer_is_admin=False
    )
    assert bob_visible.items == (alpha, finance, general)
    assert bob_visible.total == 3
    first_page = catalog.search(
        TemplateSearch(offset=0, limit=2), viewer_id=bob.id, viewer_is_admin=False
    )
    second_page = catalog.search(
        TemplateSearch(offset=2, limit=2), viewer_id=bob.id, viewer_is_admin=False
    )
    assert first_page.items + second_page.items == bob_visible.items
    assert first_page.total == second_page.total == 3

    assert catalog.search(
        TemplateSearch(name="finance %_", limit=10),
        viewer_id=bob.id,
        viewer_is_admin=False,
    ).items == (finance,)
    assert catalog.search(
        TemplateSearch(description="résumé", limit=10),
        viewer_id=bob.id,
        viewer_is_admin=False,
    ).items == (finance,)
    assert catalog.search(
        TemplateSearch(owner_id=alice.id, limit=10),
        viewer_id=bob.id,
        viewer_is_admin=False,
    ).items == (alpha, finance)
    assert not catalog.search(
        TemplateSearch(status=TemplateStatus.ARCHIVED, limit=10),
        viewer_id=bob.id,
        viewer_is_admin=False,
    ).items
    assert catalog.search(
        TemplateSearch(status=TemplateStatus.ARCHIVED, limit=10),
        viewer_id=alice.id,
        viewer_is_admin=False,
    ).items == (archived,)
    assert catalog.search(
        TemplateSearch(status=TemplateStatus.ARCHIVED, limit=10),
        viewer_id=admin.id,
        viewer_is_admin=True,
    ).items == (archived,)

    assert catalog.get(finance.id) == finance
    assert catalog.get(uuid4()) is None
    assert selections.resolve(alice.id) is None
    selections.set_system_fallback(general.id)
    assert selections.system_fallback_id() == general.id
    assert selections.resolve(alice.id) == general
    selections.set_preferred(alice.id, finance.id)
    assert selections.preferred_id(alice.id) == finance.id
    assert selections.resolve(alice.id) == finance
    selections.clear_preferred(alice.id)
    assert selections.preferred_id(alice.id) is None
    assert selections.resolve(alice.id) == general
    with pytest.raises(TemplateUnavailableError):
        selections.set_preferred(alice.id, archived.id)
    with pytest.raises(TemplateUnavailableError):
        selections.set_system_fallback(archived.id)

    choices = (alpha.id, finance.id, general.id)

    def select_preferred(index: int) -> None:
        selections.set_preferred(alice.id, choices[index % len(choices)])

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(select_preferred, range(12)))
    assert selections.preferred_id(alice.id) in choices


def exercise_template_service_contract(
    users: UserRepository,
    catalog: TemplateCatalogRepository,
    selections: TemplateSelectionRepository,
) -> None:
    """Verify actor-derived ownership and authorization over a real profile."""
    admin = User(uuid4(), "Admin", f"admin-{uuid4()}", "hash:admin", Role.ADMIN)
    alice = User(uuid4(), "Alice", f"alice-{uuid4()}", "hash:alice", Role.USER)
    bob = User(uuid4(), "Bob", f"bob-{uuid4()}", "hash:bob", Role.USER)
    for user in (admin, alice, bob):
        users.create(user)

    service = TemplateService(catalog=catalog, selections=selections)
    shared = service.create(
        alice,
        TemplateCreate(uuid4(), "Shared service template", "Visible to every user"),
    )
    forged = TemplateIdentity(
        uuid4(),
        bob.id,
        "Forged owner request",
        "Must still belong to Alice",
        TemplateStatus.ACTIVE,
    )
    actor_owned = service.create(alice, cast(TemplateCreate, forged))
    assert actor_owned.owner_id == alice.id
    assert actor_owned.owner_id != bob.id

    archived = TemplateIdentity(
        uuid4(),
        alice.id,
        "Archived service template",
        "Owner only",
        TemplateStatus.ARCHIVED,
    )
    catalog.add(archived)

    visible = service.search(bob, TemplateSearch(limit=100))
    assert shared in visible.items
    assert actor_owned in visible.items
    assert archived not in visible.items
    assert service.get_visible(bob, shared.id) == shared
    with pytest.raises(TemplateUnavailableError):
        service.get_visible(bob, archived.id)
    assert service.get_visible(alice, archived.id) == archived
    assert service.get_visible(admin, archived.id) == archived

    owner_authorization = service.authorize_mutation(alice, archived.id)
    assert owner_authorization.owner_id == alice.id
    assert not owner_authorization.administrator_intervention
    with pytest.raises(AuthenticationError):
        service.authorize_mutation(bob, archived.id)
    admin_authorization = service.authorize_mutation(admin, archived.id)
    assert admin_authorization.operation is TemplateOperation.MUTATE
    assert admin_authorization.administrator_intervention

    with pytest.raises(AuthenticationError):
        service.set_system_fallback(bob, actor_owned.id)
    fallback_authorization = service.set_system_fallback(admin, actor_owned.id)
    assert fallback_authorization.operation is TemplateOperation.SET_SYSTEM_FALLBACK
    assert fallback_authorization.administrator_intervention
    assert service.resolve(bob) == actor_owned
    service.set_preferred(bob, shared.id)
    assert service.resolve(bob) == shared
    service.clear_preferred(bob)
    assert service.resolve(bob) == actor_owned
