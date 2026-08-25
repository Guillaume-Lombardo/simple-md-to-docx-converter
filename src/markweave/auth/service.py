"""Local authentication, session, account, and authorization services."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from markweave.auth.errors import (
    AUTHENTICATION_REQUIRED,
    CSRF_REQUIRED,
    FORBIDDEN,
    INVALID_CREDENTIALS,
    PASSWORD_INVALID,
    USER_NOT_FOUND,
    USERNAME_INVALID,
    USERNAME_TAKEN,
)
from markweave.auth.models import (
    AuthenticationAuditContext,
    AuthenticationAuditOperation,
    LoginResult,
    Role,
    Session,
    User,
    normalize_username,
)
from markweave.auth.ports import (
    Clock,
    PasswordHasher,
    SessionRepository,
    TokenGenerator,
    UserRepository,
)
from markweave.auth.security import digest_token


@dataclass(frozen=True, slots=True)
class SecurityRuntime:
    """Security adapters used by the authentication use cases."""

    hasher: PasswordHasher
    tokens: TokenGenerator
    clock: Clock


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Configurable idle and absolute server-side session lifetimes."""

    idle_seconds: int
    absolute_seconds: int


class AuthorizationService:
    """Extensible owner/administrator policy boundary."""

    @staticmethod
    def require_admin(actor: User) -> None:
        if actor.role is not Role.ADMIN:
            raise FORBIDDEN.new()

    @staticmethod
    def require_owner_or_admin(actor: User, owner_id: UUID) -> None:
        if actor.id != owner_id and actor.role is not Role.ADMIN:
            raise FORBIDDEN.new()


class AuthenticationService:
    """Local account and server-side session orchestration."""

    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: SessionRepository,
        security: SecurityRuntime,
        policy: SessionPolicy,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.hasher = security.hasher
        self.tokens = security.tokens
        self.clock = security.clock
        self.idle_lifetime = timedelta(seconds=policy.idle_seconds)
        self.absolute_lifetime = timedelta(seconds=policy.absolute_seconds)

    def bootstrap_admin(self, username: str, password: str) -> User:
        normalized = normalize_username(username)
        if not normalized or not password:
            raise ValueError("Invalid bootstrap administrator configuration")
        return self.users.bootstrap_admin(
            username, normalized, self.hasher.hash(password)
        )

    def login(
        self, username: str, password: str, *, previous_session_token: str | None = None
    ) -> LoginResult:
        if previous_session_token:
            self.logout(previous_session_token)
        normalized = normalize_username(username)
        user = self.users.get_by_normalized_username(normalized) if normalized else None
        candidate_hash = (
            user.password_hash
            if user is not None and user.active
            else self.hasher.dummy_hash
        )
        valid, replacement = self.hasher.verify_and_rehash(candidate_hash, password)
        if user is None or not user.active or not valid:
            raise INVALID_CREDENTIALS.new()
        committed_user = self.users.commit_verified_login(
            user.id, user.auth_version, replacement
        )
        if committed_user is None:
            raise INVALID_CREDENTIALS.new()

        session_token = self.tokens.generate()
        csrf_token = self.tokens.generate()
        now = self.clock.now()
        absolute_expires = now + self.absolute_lifetime
        session = Session(
            token_digest=digest_token(session_token),
            csrf_digest=digest_token(csrf_token),
            user_id=committed_user.id,
            auth_version=committed_user.auth_version,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=min(now + self.idle_lifetime, absolute_expires),
            absolute_expires_at=absolute_expires,
        )
        self.sessions.create(session)
        return LoginResult(
            user=committed_user,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    def authenticate(self, session_token: str | None) -> User:
        session = self._active_session(session_token)
        user = self.users.get_by_id(session.user_id)
        if user is None or not user.active or user.auth_version != session.auth_version:
            self.sessions.revoke(session.token_digest)
            raise AUTHENTICATION_REQUIRED.new()
        now = self.clock.now()
        session.last_seen_at = now
        session.idle_expires_at = min(
            now + self.idle_lifetime, session.absolute_expires_at
        )
        self.sessions.save(session)
        return user

    def validate_csrf(self, session_token: str | None, csrf_token: str | None) -> None:
        session = self._active_session(session_token)
        candidate = digest_token(csrf_token) if csrf_token else ""
        if not secrets.compare_digest(session.csrf_digest, candidate):
            raise CSRF_REQUIRED.new()

    def logout(self, session_token: str | None) -> None:
        if session_token:
            self.sessions.revoke(digest_token(session_token))

    def create_user(self, actor: User, username: str, password: str) -> User:
        AuthorizationService.require_admin(actor)
        normalized = normalize_username(username)
        if not normalized:
            raise USERNAME_INVALID.new()
        if not password:
            raise PASSWORD_INVALID.new()
        if self.users.get_by_normalized_username(normalized) is not None:
            raise USERNAME_TAKEN.new()
        user = User(
            id=uuid4(),
            username=username.strip(),
            normalized_username=normalized,
            password_hash=self.hasher.hash(password),
            role=Role.USER,
        )
        try:
            self.users.create(
                user,
                audit=self._audit(actor, AuthenticationAuditOperation.CREATE),
            )
        except KeyError:
            raise USERNAME_TAKEN.new() from None
        return user

    def list_users(self, actor: User) -> list[User]:
        AuthorizationService.require_admin(actor)
        return self.users.list()

    def set_active(self, actor: User, user_id: UUID, *, active: bool) -> User:
        AuthorizationService.require_admin(actor)
        operation = (
            AuthenticationAuditOperation.REACTIVATE
            if active
            else AuthenticationAuditOperation.DEACTIVATE
        )
        user = self.users.update_security(
            user_id, active=active, audit=self._audit(actor, operation)
        )
        if user is None:
            raise USER_NOT_FOUND.new()
        self.sessions.revoke_user(user.id)
        return user

    def reset_password(self, actor: User, user_id: UUID, password: str) -> None:
        AuthorizationService.require_admin(actor)
        if not password:
            raise PASSWORD_INVALID.new()
        user = self.users.update_security(
            user_id,
            password_hash=self.hasher.hash(password),
            audit=self._audit(actor, AuthenticationAuditOperation.RESET_PASSWORD),
        )
        if user is None:
            raise USER_NOT_FOUND.new()
        self.sessions.revoke_user(user.id)

    def _audit(
        self, actor: User, operation: AuthenticationAuditOperation
    ) -> AuthenticationAuditContext:
        return AuthenticationAuditContext(
            uuid4(), actor.id, operation, self.clock.now()
        )

    def _active_session(self, session_token: str | None) -> Session:
        if not session_token:
            raise AUTHENTICATION_REQUIRED.new()
        digest = digest_token(session_token)
        session = self.sessions.get(digest)
        if session is None:
            raise AUTHENTICATION_REQUIRED.new()
        now = self.clock.now()
        if now >= session.idle_expires_at or now >= session.absolute_expires_at:
            self.sessions.revoke(digest)
            raise AUTHENTICATION_REQUIRED.new()
        return session
