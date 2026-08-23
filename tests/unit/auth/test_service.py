"""Unit tests for local accounts, authorization, and session policy."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import uuid4

import pytest

from md_converter.auth.errors import AuthenticationError
from md_converter.auth.memory import MemorySessionRepository, MemoryUserRepository
from md_converter.auth.models import Role, User, normalize_username
from md_converter.auth.security import digest_token
from md_converter.auth.service import (
    AuthenticationService,
    AuthorizationService,
    SecurityRuntime,
    SessionPolicy,
)
from md_converter.config import ConfigurationError


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
def test_authorization_and_missing_account_fail_stably() -> None:
    service, hasher, _ = build_service()
    admin = service.bootstrap_admin("admin", "correct")
    user = User(uuid4(), "user", "user", hasher.hash("password"), Role.USER)
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
