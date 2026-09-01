"""Shared assembled-service idle-session enforcement contract."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from markweave.auth.errors import AuthenticationError
from markweave.auth.models import IdleSessionPolicy
from markweave.auth.security import digest_token
from markweave.persistence.schema import IdleSessionPolicyAuditRow, UserRow


class ContractClock:
    """Deterministic clock installed into an assembled authentication service."""

    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, **values: float) -> None:
        self.value += timedelta(**values)


def _rejected(operation) -> None:
    with pytest.raises(AuthenticationError) as caught:
        operation()
    assert caught.value.code == "AUTHENTICATION_REQUIRED"


def exercise_assembled_idle_session_policy_contract(app) -> None:
    """Exercise role enforcement, concurrency, audit, revocation, and ceilings."""
    components = app.state.components
    auth = components.authentication
    clock = ContractClock()
    auth.clock = clock
    admin = auth.users.get_by_normalized_username("admin")
    assert admin is not None
    auth.create_user(admin, "policy-alice", "alice-password")
    bob = auth.create_user(admin, "policy-bob", "bob-password")
    assert auth.get_idle_session_policy(admin) == IdleSessionPolicy()

    admin_login = auth.login("admin", "admin-password")
    alice_login = auth.login("policy-alice", "alice-password")
    admin_session = auth.sessions.get(digest_token(admin_login.session_token))
    alice_session = auth.sessions.get(digest_token(alice_login.session_token))
    assert admin_session is not None and alice_session is not None
    assert admin_session.idle_expires_at - clock.now() == timedelta(minutes=15)
    assert alice_session.idle_expires_at - clock.now() == timedelta(minutes=30)

    assert auth.update_idle_session_policy(
        admin, user_idle_minutes=5, admin_idle_minutes=5, expected_revision=0
    ) == IdleSessionPolicy(5, 5, 1)
    clock.advance(minutes=6)
    _rejected(lambda: auth.authenticate(admin_login.session_token))
    _rejected(lambda: auth.authenticate(alice_login.session_token))
    assert auth.update_idle_session_policy(
        admin, user_idle_minutes=300, admin_idle_minutes=60, expected_revision=1
    ) == IdleSessionPolicy(300, 60, 2)
    _rejected(lambda: auth.authenticate(alice_login.session_token))

    bob_login = auth.login("policy-bob", "bob-password")
    assert components.owned_engines
    engine = components.owned_engines[0]
    with engine.begin() as connection:
        connection.execute(
            update(UserRow).where(UserRow.id == str(bob.id)).values(role="admin")
        )
    assert auth.update_idle_session_policy(
        admin, user_idle_minutes=300, admin_idle_minutes=5, expected_revision=2
    ) == IdleSessionPolicy(300, 5, 3)
    clock.advance(minutes=6)
    _rejected(lambda: auth.authenticate(bob_login.session_token))

    expected_revision = 3
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                lambda minutes: auth.update_idle_session_policy(
                    admin,
                    user_idle_minutes=minutes,
                    admin_idle_minutes=5,
                    expected_revision=expected_revision,
                ),
                (5, 6),
            )
        )
    assert sum(outcome is not None for outcome in outcomes) == 1
    assert auth.get_idle_session_policy(admin).revision == 4

    fresh = auth.login("policy-alice", "alice-password")
    auth.logout(fresh.session_token)
    _rejected(lambda: auth.authenticate(fresh.session_token))
    absolute = auth.login("policy-alice", "alice-password")
    clock.advance(hours=8)
    _rejected(lambda: auth.authenticate(absolute.session_token))

    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(IdleSessionPolicyAuditRow)
            )
            == 4
        )
