"""Temporary thread-safe in-memory adapters used before T12 persistence."""

from __future__ import annotations

import builtins
from dataclasses import replace
from threading import RLock
from uuid import UUID, uuid4

from markweave.auth.models import AuthenticationAuditContext, Role, Session, User
from markweave.config import ConfigurationError


class MemoryUserRepository:
    """Thread-safe account adapter with atomic administrator bootstrap."""

    def __init__(self) -> None:
        self._users: dict[UUID, User] = {}
        self._normalized_ids: dict[str, UUID] = {}
        self._lock = RLock()

    def bootstrap_admin(
        self, username: str, normalized_username: str, password_hash: str
    ) -> User:
        """Create the initial admin once without changing an existing password."""
        with self._lock:
            existing_id = self._normalized_ids.get(normalized_username)
            if existing_id is not None:
                existing = self._users[existing_id]
                if existing.role is not Role.ADMIN:
                    raise ConfigurationError(
                        "Initial administrator conflicts with an account"
                    )
                return replace(existing)
            user = User(
                id=uuid4(),
                username=username.strip(),
                normalized_username=normalized_username,
                password_hash=password_hash,
                role=Role.ADMIN,
            )
            self._users[user.id] = replace(user)
            self._normalized_ids[normalized_username] = user.id
            return user

    def create(
        self, user: User, *, audit: AuthenticationAuditContext | None = None
    ) -> None:
        del audit
        with self._lock:
            if user.normalized_username in self._normalized_ids:
                raise KeyError(user.normalized_username)
            self._users[user.id] = replace(user)
            self._normalized_ids[user.normalized_username] = user.id

    def get_by_id(self, user_id: UUID) -> User | None:
        with self._lock:
            user = self._users.get(user_id)
            return replace(user) if user is not None else None

    def get_by_normalized_username(self, normalized_username: str) -> User | None:
        with self._lock:
            user_id = self._normalized_ids.get(normalized_username)
            user = self._users.get(user_id) if user_id is not None else None
            return replace(user) if user is not None else None

    def list(self) -> builtins.list[User]:
        with self._lock:
            return sorted(
                (replace(user) for user in self._users.values()),
                key=lambda user: user.normalized_username,
            )

    def commit_verified_login(
        self,
        user_id: UUID,
        expected_auth_version: int,
        replacement_hash: str | None,
    ) -> User | None:
        """Compare-and-set the verified snapshot before session issuance."""
        with self._lock:
            user = self._users.get(user_id)
            if (
                user is None
                or not user.active
                or user.auth_version != expected_auth_version
            ):
                return None
            if replacement_hash is not None:
                user = replace(user, password_hash=replacement_hash)
                self._users[user.id] = user
            return replace(user)

    def update_security(
        self,
        user_id: UUID,
        *,
        active: bool | None = None,
        password_hash: str | None = None,
        audit: AuthenticationAuditContext | None = None,
    ) -> User | None:
        """Atomically change account security state and invalidate older sessions."""
        del audit
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                return None
            user = replace(
                user,
                active=user.active if active is None else active,
                password_hash=(
                    user.password_hash if password_hash is None else password_hash
                ),
                auth_version=user.auth_version + 1,
            )
            self._users[user.id] = user
            return replace(user)


class MemorySessionRepository:
    """Thread-safe session adapter storing only token digests."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = RLock()

    def create(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.token_digest] = replace(session)

    def get(self, token_digest: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(token_digest)
            return replace(session) if session is not None else None

    def save(self, session: Session) -> None:
        with self._lock:
            if session.token_digest in self._sessions:
                self._sessions[session.token_digest] = replace(session)

    def revoke(self, token_digest: str) -> None:
        with self._lock:
            self._sessions.pop(token_digest, None)

    def revoke_user(self, user_id: UUID) -> None:
        with self._lock:
            self._sessions = {
                digest: session
                for digest, session in self._sessions.items()
                if session.user_id != user_id
            }


class MemoryReadinessProbe:
    """Cheap readiness adapter toggled by tests and future profile assembly."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def is_ready(self) -> bool:
        return self.ready
