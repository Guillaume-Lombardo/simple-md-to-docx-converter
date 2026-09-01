"""Unit tests for local accounts, authorization, and session policy."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from markweave.auth.errors import AuthenticationError
from markweave.auth.memory import (
    MemoryIdleSessionPolicyRepository,
    MemorySessionRepository,
    MemoryUserRepository,
)
from markweave.auth.models import (
    IdleSessionPolicy,
    IdleSessionPolicyAudit,
    IdleSessionPolicyOperation,
    Role,
    User,
    normalize_username,
)
from markweave.auth.policy_errors import IdleSessionPolicyAbsoluteLimitError
from markweave.auth.security import digest_token
from markweave.auth.service import (
    AuthenticationService,
    AuthorizationService,
    SecurityRuntime,
    SessionPolicy,
)
from markweave.config import ConfigurationError


class FakeHasher:
    """Fast deterministic password adapter with observable verification calls."""

    dummy_hash = "hash:dummy"

    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []
        self.on_verify: Callable[[], None] | None = None

    def hash(self, password: str) -> str:
        return f"hash:{password}"

    def verify_and_rehash(
        self, password_hash: str, password: str
    ) -> tuple[bool, str | None]:
        self.verifications.append((password_hash, password))
        if self.on_verify is not None:
            callback, self.on_verify = self.on_verify, None
            callback()
        valid = password_hash in {f"hash:{password}", f"old:{password}"}
        replacement = (
            f"hash:{password}" if valid and password_hash.startswith("old:") else None
        )
        return valid, replacement


class SequenceTokens:
    """Unique deterministic tokens for unit tests."""

    def __init__(self) -> None:
        self._numbers = count(1)
        self.on_generate: Callable[[], None] | None = None

    def generate(self) -> str:
        if self.on_generate is not None:
            callback, self.on_generate = self.on_generate, None
            callback()
        return f"token-{next(self._numbers)}"


class FakeClock:
    """Mutable injected UTC clock."""

    def __init__(self) -> None:
        self.value = datetime(2026, 8, 23, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


def build_service() -> tuple[AuthenticationService, FakeHasher, FakeClock]:
    hasher = FakeHasher()
    clock = FakeClock()
    service = AuthenticationService(
        users=MemoryUserRepository(),
        sessions=MemorySessionRepository(),
        security=SecurityRuntime(hasher=hasher, tokens=SequenceTokens(), clock=clock),
        policy=SessionPolicy(idle_seconds=30, absolute_seconds=100),
    )
    return service, hasher, clock


def build_role_policy_service() -> tuple[
    AuthenticationService,
    MemoryIdleSessionPolicyRepository,
    FakeClock,
]:
    hasher = FakeHasher()
    clock = FakeClock()
    policies = MemoryIdleSessionPolicyRepository()
    service = AuthenticationService(
        users=MemoryUserRepository(),
        sessions=MemorySessionRepository(),
        security=SecurityRuntime(hasher=hasher, tokens=SequenceTokens(), clock=clock),
        policy=SessionPolicy(absolute_seconds=8 * 60 * 60),
        idle_policies=policies,
    )
    return service, policies, clock


def assert_error(code: str, operation: Callable[[], object]) -> None:
    with pytest.raises(AuthenticationError) as caught:
        operation()
    assert caught.value.code == code


@pytest.mark.unit
def test_username_normalization_and_atomic_bootstrap_do_not_reset_password() -> None:
    service, _, _ = build_service()
    with ThreadPoolExecutor(max_workers=8) as executor:
        admins = list(
            executor.map(
                lambda index: service.bootstrap_admin(
                    "  \uff21dmin  ", f"secret-{index}"
                ),
                range(24),
            )
        )

    assert normalize_username("  \uff21dmin  ") == "admin"
    assert len({admin.id for admin in admins}) == 1
    assert len(service.users.list()) == 1
    assert admins[0].password_hash in {f"hash:secret-{index}" for index in range(24)}
    original_hash = admins[0].password_hash
    assert (
        service.bootstrap_admin("ADMIN", "replacement").password_hash == original_hash
    )


@pytest.mark.unit
def test_bootstrap_rejects_invalid_values_and_non_admin_collision() -> None:
    service, hasher, _ = build_service()
    with pytest.raises(ValueError, match="Invalid bootstrap"):
        service.bootstrap_admin(" ", "password")
    user = User(uuid4(), "admin", "admin", hasher.hash("password"), Role.USER)
    service.users.create(user)
    with pytest.raises(ConfigurationError, match="conflicts"):
        service.bootstrap_admin("Admin", "replacement")


@pytest.mark.unit
def test_login_is_anti_enumerating_and_rehashes_only_a_successful_password() -> None:
    service, hasher, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    legacy_hash = "old:" + "correct"
    service.users.update_security(admin.id, password_hash=legacy_hash)

    for username, password in [("missing", "guess"), ("admin", "wrong")]:
        assert_error(
            "INVALID_CREDENTIALS",
            lambda username=username, password=password: service.login(
                username, password
            ),
        )
    assert hasher.verifications[0] == (hasher.dummy_hash, "guess")
    stored = service.users.get_by_id(admin.id)
    assert stored is not None
    assert stored.password_hash == legacy_hash

    result = service.login("ADMIN", "correct")
    assert result.user.password_hash == hasher.hash("correct")
    assert result.session_token != result.csrf_token


@pytest.mark.unit
def test_inactive_account_uses_dummy_verification_and_cannot_login() -> None:
    service, hasher, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    service.users.update_security(admin.id, active=False)

    assert_error("INVALID_CREDENTIALS", lambda: service.login("admin", "correct"))
    assert hasher.verifications[-1] == (hasher.dummy_hash, "correct")


@pytest.mark.unit
def test_session_rotation_idle_and_absolute_boundaries() -> None:
    service, _, clock = build_service()
    service.bootstrap_admin("admin", "correct")
    first = service.login("admin", "correct")
    second = service.login(
        "admin", "correct", previous_session_token=first.session_token
    )
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(first.session_token)
    )

    clock.advance(seconds=29)
    assert service.authenticate(second.session_token).username == "admin"
    clock.advance(seconds=30)
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(second.session_token)
    )

    absolute = service.login("admin", "correct")
    for _ in range(3):
        clock.advance(seconds=25)
        service.authenticate(absolute.session_token)
    clock.advance(seconds=25)
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(absolute.session_token)
    )


@pytest.mark.unit
def test_csrf_is_session_bound_and_logout_is_idempotent() -> None:
    service, _, _ = build_service()
    service.bootstrap_admin("admin", "correct")
    first = service.login("admin", "correct")
    second = service.login("admin", "correct")

    service.validate_csrf(first.session_token, first.csrf_token)
    assert_error(
        "CSRF_REQUIRED",
        lambda: service.validate_csrf(first.session_token, second.csrf_token),
    )
    assert_error(
        "CSRF_REQUIRED", lambda: service.validate_csrf(first.session_token, None)
    )
    service.logout(first.session_token)
    service.logout(first.session_token)
    service.logout(None)
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(first.session_token)
    )


@pytest.mark.unit
def test_admin_account_lifecycle_revokes_all_sessions() -> None:
    service, _, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = service.create_user(admin, "  \uff22ob  ", "first")
    assert user.username == "\uff22ob"
    assert service.list_users(admin) == [admin, user]
    assert_error("USERNAME_TAKEN", lambda: service.create_user(admin, "bob", "other"))
    assert_error("USERNAME_INVALID", lambda: service.create_user(admin, " ", "other"))
    assert_error("PASSWORD_INVALID", lambda: service.create_user(admin, "other", ""))

    sessions = [service.login("bob", "first") for _ in range(2)]
    service.set_active(admin, user.id, active=False)
    for session in sessions:
        assert_error(
            "AUTHENTICATION_REQUIRED",
            lambda session=session: service.authenticate(session.session_token),
        )
    assert_error("INVALID_CREDENTIALS", lambda: service.login("bob", "first"))

    service.set_active(admin, user.id, active=True)
    active = service.login("bob", "first")
    service.reset_password(admin, user.id, "second")
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(active.session_token)
    )
    assert_error("INVALID_CREDENTIALS", lambda: service.login("bob", "first"))
    assert service.login("bob", "second").user.id == user.id


@pytest.mark.unit
def test_required_password_change_restricts_session_until_atomic_renewal() -> None:
    service, _, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = service.create_user(
        admin,
        "Alice",
        "temporary",
        password_change_required=True,
    )

    login = service.login("alice", "temporary")
    assert login.user.password_change_required
    assert (
        service.authenticate(login.session_token, allow_password_change=True).id
        == user.id
    )
    assert_error(
        "PASSWORD_CHANGE_REQUIRED",
        lambda: service.authenticate(login.session_token),
    )
    service.validate_csrf(login.session_token, login.csrf_token)
    assert_error(
        "PASSWORD_CONFIRMATION_INVALID",
        lambda: service.change_password(user, "new", "different"),
    )

    service.change_password(user, "new", "new")
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(login.session_token, allow_password_change=True),
    )
    assert_error("INVALID_CREDENTIALS", lambda: service.login("alice", "temporary"))
    renewed = service.login("alice", "new")
    assert not renewed.user.password_change_required


@pytest.mark.unit
def test_administrator_can_require_password_change_and_revoke_sessions() -> None:
    service, _, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = service.create_user(admin, "Alice", "password")
    active = service.login("alice", "password")

    required = service.set_password_change_required(admin, user.id, required=True)
    assert required.password_change_required
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(active.session_token, allow_password_change=True),
    )
    reset = service.login("alice", "password")
    service.reset_password(
        admin,
        user.id,
        "temporary",
        password_change_required=True,
    )
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(reset.session_token, allow_password_change=True),
    )
    assert service.login("alice", "temporary").user.password_change_required


@pytest.mark.unit
def test_stale_renewal_cannot_overwrite_an_administrator_password_reset() -> None:
    service, _, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = service.create_user(
        admin,
        "Alice",
        "old-password",
        password_change_required=True,
    )
    restricted = service.login("alice", "old-password")
    stale_actor = service.authenticate(
        restricted.session_token, allow_password_change=True
    )

    service.reset_password(
        admin,
        user.id,
        "administrator-reset",
        password_change_required=True,
    )

    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.change_password(
            stale_actor, "stale-request-wins", "stale-request-wins"
        ),
    )
    assert_error(
        "INVALID_CREDENTIALS",
        lambda: service.login("alice", "stale-request-wins"),
    )
    assert service.login("alice", "administrator-reset").user.password_change_required


@pytest.mark.unit
def test_startup_provisioning_creates_then_replaces_memory_accounts(
    tmp_path: Path,
) -> None:
    service, hasher, _ = build_service()
    admin = service.bootstrap_admin("admin", "original")
    active = service.login("admin", "original")
    source = tmp_path / "users.csv"
    source.write_text(
        "username,password,role,active,password_change_required\n"
        "Admin,replacement,admin,true,true\n"
        "Alice,temporary,user,false,false\n",
        encoding="utf-8",
    )

    first = service.provision_users(source)
    assert [user.normalized_username for user in first] == ["admin", "alice"]
    assert first[0].id == admin.id
    assert first[0].password_hash == hasher.hash("replacement")
    assert first[0].auth_version == 1
    assert first[0].password_change_required
    assert not first[1].active
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(active.session_token, allow_password_change=True),
    )

    second = service.provision_users(source)
    assert second[0].id == admin.id
    assert second[0].auth_version == 2
    assert second[1].id == first[1].id
    assert second[1].auth_version == 1


@pytest.mark.unit
def test_authorization_and_missing_account_fail_stably() -> None:
    service, hasher, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = User(uuid4(), "user", "user", hasher.hash("password"), Role.USER)
    service.users.create(user)
    with pytest.raises(KeyError, match="user"):
        service.users.create(user)

    AuthorizationService.require_admin(admin)
    AuthorizationService.require_owner_or_admin(user, user.id)
    AuthorizationService.require_owner_or_admin(admin, user.id)
    assert_error("FORBIDDEN", lambda: AuthorizationService.require_admin(user))
    assert_error(
        "FORBIDDEN", lambda: AuthorizationService.require_owner_or_admin(user, admin.id)
    )
    assert_error(
        "USER_NOT_FOUND", lambda: service.set_active(admin, uuid4(), active=False)
    )
    assert_error("PASSWORD_INVALID", lambda: service.reset_password(admin, user.id, ""))
    assert_error(
        "USER_NOT_FOUND", lambda: service.reset_password(admin, uuid4(), "replacement")
    )
    assert_error(
        "USER_NOT_FOUND",
        lambda: service.set_password_change_required(admin, uuid4(), required=True),
    )
    assert_error("PASSWORD_INVALID", lambda: service.change_password(user, "", ""))
    missing = User(uuid4(), "missing", "missing", hasher.hash("old"), Role.USER)
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.change_password(missing, "replacement", "replacement"),
    )
    assert_error("AUTHENTICATION_REQUIRED", lambda: service.authenticate(None))


@pytest.mark.unit
def test_session_repository_never_indexes_raw_tokens() -> None:
    service, _, _ = build_service()
    service.bootstrap_admin("admin", "correct")
    result = service.login("admin", "correct")
    assert service.sessions.get(result.session_token) is None
    assert service.sessions.get(digest_token(result.session_token)) is not None


@pytest.mark.unit
@pytest.mark.parametrize("needs_rehash", [False, True])
def test_reset_during_password_verification_rejects_stale_login(
    needs_rehash: bool,
) -> None:
    service, hasher, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    if needs_rehash:
        service.users.update_security(admin.id, password_hash="old:" + "correct")
    hasher.on_verify = lambda: service.reset_password(admin, admin.id, "replacement")

    assert_error("INVALID_CREDENTIALS", lambda: service.login("admin", "correct"))
    current = service.users.get_by_id(admin.id)
    assert current is not None
    assert current.password_hash == hasher.hash("replacement")
    assert current.auth_version == (2 if needs_rehash else 1)


@pytest.mark.unit
def test_security_change_after_login_cas_makes_late_session_unusable() -> None:
    service, _, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    assert isinstance(service.tokens, SequenceTokens)

    def disable_during_token_generation() -> None:
        service.set_active(admin, admin.id, active=False)

    service.tokens.on_generate = disable_during_token_generation

    result = service.login("admin", "correct")
    assert result.user.auth_version == 0
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(result.session_token)
    )


@pytest.mark.unit
def test_role_defaults_tightening_role_change_and_relaxation_never_revive() -> None:
    service, _, clock = build_role_policy_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = service.create_user(admin, "alice", "correct")
    assert service.effective_idle_minutes(Role.ADMIN) == 15
    assert service.effective_idle_minutes(Role.USER) == 30

    admin_login = service.login("admin", "correct")
    user_login = service.login("alice", "correct")
    admin_session = service.sessions.get(digest_token(admin_login.session_token))
    user_session = service.sessions.get(digest_token(user_login.session_token))
    assert admin_session is not None and user_session is not None
    assert admin_session.idle_expires_at - admin_session.created_at == timedelta(
        minutes=15
    )
    assert user_session.idle_expires_at - user_session.created_at == timedelta(
        minutes=30
    )

    assert service.update_idle_session_policy(
        admin,
        user_idle_minutes=5,
        admin_idle_minutes=10,
        expected_revision=0,
    ) == IdleSessionPolicy(5, 10, 1)
    assert service.effective_idle_minutes(Role.ADMIN) == 10
    assert service.effective_idle_minutes(Role.USER) == 5
    clock.advance(minutes=6)
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(user_login.session_token),
    )
    assert service.update_idle_session_policy(
        admin,
        user_idle_minutes=300,
        admin_idle_minutes=60,
        expected_revision=1,
    ) == IdleSessionPolicy(300, 60, 2)
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(user_login.session_token),
    )

    fresh_user = service.login("alice", "correct")
    users = service.users
    assert isinstance(users, MemoryUserRepository)
    stored = users._users[user.id]
    users._users[user.id] = replace(stored, role=Role.ADMIN)
    clock.advance(minutes=61)
    assert_error(
        "AUTHENTICATION_REQUIRED",
        lambda: service.authenticate(fresh_user.session_token),
    )


@pytest.mark.unit
def test_policy_update_is_atomic_versioned_audited_and_absolute_is_hard_ceiling() -> (
    None
):
    service, policies, clock = build_role_policy_service()
    admin = service.bootstrap_admin("admin", "correct")
    assert service.get_idle_session_policy(admin) == IdleSessionPolicy()
    updated = service.update_idle_session_policy(
        admin,
        user_idle_minutes=300,
        admin_idle_minutes=60,
        expected_revision=0,
    )
    assert updated == IdleSessionPolicy(300, 60, 1)
    assert (
        service.update_idle_session_policy(
            admin,
            user_idle_minutes=5,
            admin_idle_minutes=5,
            expected_revision=0,
        )
        is None
    )
    assert policies.get() == updated
    audit = policies.audits[0]
    assert audit.actor_id == admin.id
    assert (audit.old_user_idle_minutes, audit.old_admin_idle_minutes) == (30, 15)
    assert (audit.new_user_idle_minutes, audit.new_admin_idle_minutes) == (300, 60)
    assert audit.revision == 1

    login = service.login("admin", "correct")
    clock.advance(hours=8)
    assert_error(
        "AUTHENTICATION_REQUIRED", lambda: service.authenticate(login.session_token)
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("user_idle_minutes", True),
        ("user_idle_minutes", 5.5),
        ("user_idle_minutes", "5"),
        ("user_idle_minutes", 4),
        ("user_idle_minutes", 301),
        ("admin_idle_minutes", False),
        ("admin_idle_minutes", 5.5),
        ("admin_idle_minutes", "5"),
        ("admin_idle_minutes", 4),
        ("admin_idle_minutes", 61),
        ("revision", True),
        ("revision", 1.5),
        ("revision", "1"),
        ("revision", -1),
    ),
)
def test_idle_session_policy_requires_actual_integers(
    field: str, value: object
) -> None:
    values: dict[str, Any] = {
        "user_idle_minutes": 30,
        "admin_idle_minutes": 15,
        "revision": 0,
    }
    values[field] = value
    with pytest.raises(ValueError):
        IdleSessionPolicy(**values)


@pytest.mark.unit
def test_memory_policy_audit_derives_new_values_from_persisted_policy() -> None:
    repository = MemoryIdleSessionPolicyRepository()
    actor = uuid4()
    updated = repository.update(
        IdleSessionPolicy(300, 60),
        expected_revision=0,
        audit=IdleSessionPolicyAudit(
            uuid4(),
            actor,
            IdleSessionPolicyOperation.UPDATE,
            30,
            15,
            5,
            5,
            1,
            datetime.now(UTC),
        ),
    )

    assert updated == IdleSessionPolicy(300, 60, 1)
    assert (
        repository.audits[0].new_user_idle_minutes,
        repository.audits[0].new_admin_idle_minutes,
    ) == (300, 60)


@pytest.mark.unit
@pytest.mark.parametrize(("user_minutes", "admin_minutes"), ((11, 5), (5, 11)))
def test_policy_rejects_each_role_above_the_absolute_lifetime(
    user_minutes: int, admin_minutes: int
) -> None:
    service, _policies, _clock = build_role_policy_service()
    service.absolute_lifetime = timedelta(minutes=10)
    admin = service.bootstrap_admin("admin", "correct")

    with pytest.raises(IdleSessionPolicyAbsoluteLimitError):
        service.update_idle_session_policy(
            admin,
            user_idle_minutes=user_minutes,
            admin_idle_minutes=admin_minutes,
            expected_revision=0,
        )
