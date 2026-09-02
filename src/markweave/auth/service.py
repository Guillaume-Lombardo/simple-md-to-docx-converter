"""Local authentication, session, account, and authorization services."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from pathlib import Path
from uuid import UUID, uuid4

from markweave.auth.errors import (
    AUTHENTICATION_REQUIRED,
    CSRF_REQUIRED,
    FORBIDDEN,
    INVALID_CREDENTIALS,
    PASSWORD_CHANGE_REQUIRED,
    PASSWORD_CONFIRMATION_INVALID,
    PASSWORD_INVALID,
    USER_NOT_FOUND,
    USERNAME_INVALID,
    USERNAME_TAKEN,
)
from markweave.auth.memory import MemoryIdleSessionPolicyRepository
from markweave.auth.models import (
    AuthenticationAuditContext,
    AuthenticationAuditOperation,
    IdleSessionPolicy,
    IdleSessionPolicyAudit,
    IdleSessionPolicyOperation,
    LoginResult,
    ProvisionedUser,
    Role,
    Session,
    User,
    normalize_username,
)
from markweave.auth.policy_errors import IdleSessionPolicyAbsoluteLimitError
from markweave.auth.ports import (
    Clock,
    IdleSessionPolicyRepository,
    PasswordHasher,
    SessionRepository,
    TokenGenerator,
    UserRepository,
)
from markweave.auth.provisioning import load_user_provisioning_csv
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

    absolute_seconds: int
    idle_seconds: int | None = None


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
        idle_policies: IdleSessionPolicyRepository | None = None,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.hasher = security.hasher
        self.tokens = security.tokens
        self.clock = security.clock
        self._legacy_idle_lifetime = (
            timedelta(seconds=policy.idle_seconds)
            if idle_policies is None and policy.idle_seconds is not None
            else None
        )
        if idle_policies is None:
            idle_policies = MemoryIdleSessionPolicyRepository()
        self.idle_policies = idle_policies
        self.absolute_lifetime = timedelta(seconds=policy.absolute_seconds)

    def bootstrap_admin(self, username: str, password: str) -> User:
        normalized = normalize_username(username)
        if not normalized or not password:
            raise ValueError("Invalid bootstrap administrator configuration")
        return self.users.bootstrap_admin(
            username, normalized, self.hasher.hash(password)
        )

    def provision_users(self, path: Path) -> list[User]:
        """Validate, hash, and atomically apply a startup CSV batch."""
        inputs = load_user_provisioning_csv(path)
        records = [
            ProvisionedUser(
                username=record.username,
                normalized_username=record.normalized_username,
                password_hash=self.hasher.hash(record.password),
                role=record.role,
                active=record.active,
                password_change_required=record.password_change_required,
            )
            for record in inputs
        ]
        users = self.users.provision(records, self.clock.now())
        for user in users:
            self.sessions.revoke_user(user.id)
        return users

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
        idle_lifetime = self._idle_lifetime(committed_user.role)
        session = Session(
            token_digest=digest_token(session_token),
            csrf_digest=digest_token(csrf_token),
            user_id=committed_user.id,
            auth_version=committed_user.auth_version,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=min(now + idle_lifetime, absolute_expires),
            absolute_expires_at=absolute_expires,
        )
        self.sessions.create(session)
        return LoginResult(
            user=committed_user,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    def authenticate(
        self, session_token: str | None, *, allow_password_change: bool = False
    ) -> User:
        session = self._stored_session(session_token)
        user = self.users.get_by_id(session.user_id)
        if user is None or not user.active or user.auth_version != session.auth_version:
            self.sessions.revoke(session.token_digest)
            raise AUTHENTICATION_REQUIRED.new()
        now = self.clock.now()
        current_idle_expiry = min(
            session.last_seen_at + self._idle_lifetime(user.role),
            session.absolute_expires_at,
        )
        if now >= session.idle_expires_at or now >= current_idle_expiry:
            self.sessions.revoke(session.token_digest)
            raise AUTHENTICATION_REQUIRED.new()
        if user.password_change_required and not allow_password_change:
            raise PASSWORD_CHANGE_REQUIRED.new()
        session.last_seen_at = now
        session.idle_expires_at = min(
            now + self._idle_lifetime(user.role), session.absolute_expires_at
        )
        self.sessions.save(session)
        return user

    def validate_csrf(self, session_token: str | None, csrf_token: str | None) -> None:
        session = self._stored_session(session_token)
        candidate = digest_token(csrf_token) if csrf_token else ""
        if not secrets.compare_digest(session.csrf_digest, candidate):
            raise CSRF_REQUIRED.new()

    def logout(self, session_token: str | None) -> None:
        if session_token:
            self.sessions.revoke(digest_token(session_token))

    def create_user(
        self,
        actor: User,
        username: str,
        password: str,
        *,
        password_change_required: bool = False,
    ) -> User:
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
            password_change_required=password_change_required,
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

    def reset_password(
        self,
        actor: User,
        user_id: UUID,
        password: str,
        *,
        password_change_required: bool = False,
    ) -> None:
        AuthorizationService.require_admin(actor)
        if not password:
            raise PASSWORD_INVALID.new()
        user = self.users.update_security(
            user_id,
            password_hash=self.hasher.hash(password),
            password_change_required=password_change_required,
            audit=self._audit(actor, AuthenticationAuditOperation.RESET_PASSWORD),
        )
        if user is None:
            raise USER_NOT_FOUND.new()
        self.sessions.revoke_user(user.id)

    def set_password_change_required(
        self, actor: User, user_id: UUID, *, required: bool
    ) -> User:
        AuthorizationService.require_admin(actor)
        user = self.users.update_security(
            user_id,
            password_change_required=required,
            audit=self._audit(
                actor, AuthenticationAuditOperation.REQUIRE_PASSWORD_CHANGE
            ),
        )
        if user is None:
            raise USER_NOT_FOUND.new()
        self.sessions.revoke_user(user.id)
        return user

    def change_password(
        self,
        user: User,
        password: str,
        confirmation: str,
    ) -> None:
        if not password:
            raise PASSWORD_INVALID.new()
        if password != confirmation:
            raise PASSWORD_CONFIRMATION_INVALID.new()
        changed = self.users.commit_password_change(
            user.id,
            user.auth_version,
            self.hasher.hash(password),
            self._audit(user, AuthenticationAuditOperation.CHANGE_PASSWORD),
        )
        if changed is None:
            raise AUTHENTICATION_REQUIRED.new()
        self.sessions.revoke_user(user.id)

    def get_idle_session_policy(self, actor: User) -> IdleSessionPolicy:
        """Return the effective persisted/default policy to an administrator."""
        AuthorizationService.require_admin(actor)
        return self.idle_policies.get()

    def effective_idle_minutes(self, role: Role) -> int:
        """Return the server-enforced inactivity duration for one effective role."""
        seconds = self._idle_lifetime(role).total_seconds()
        return max(1, ceil(seconds / 60))

    def update_idle_session_policy(
        self,
        actor: User,
        *,
        user_idle_minutes: int,
        admin_idle_minutes: int,
        expected_revision: int,
    ) -> IdleSessionPolicy | None:
        """Atomically replace both role durations and append audit evidence."""
        AuthorizationService.require_admin(actor)
        if (
            user_idle_minutes * 60 > self.absolute_lifetime.total_seconds()
            or admin_idle_minutes * 60 > self.absolute_lifetime.total_seconds()
        ):
            raise IdleSessionPolicyAbsoluteLimitError
        current = self.idle_policies.get()
        proposed = IdleSessionPolicy(
            user_idle_minutes=user_idle_minutes,
            admin_idle_minutes=admin_idle_minutes,
            revision=expected_revision,
        )
        audit = IdleSessionPolicyAudit(
            id=uuid4(),
            actor_id=actor.id,
            operation=IdleSessionPolicyOperation.UPDATE,
            old_user_idle_minutes=current.user_idle_minutes,
            old_admin_idle_minutes=current.admin_idle_minutes,
            new_user_idle_minutes=proposed.user_idle_minutes,
            new_admin_idle_minutes=proposed.admin_idle_minutes,
            revision=expected_revision + 1,
            created_at=self.clock.now(),
        )
        return self.idle_policies.update(
            proposed,
            expected_revision=expected_revision,
            audit=audit,
        )

    def _audit(
        self, actor: User, operation: AuthenticationAuditOperation
    ) -> AuthenticationAuditContext:
        return AuthenticationAuditContext(
            uuid4(), actor.id, operation, self.clock.now()
        )

    def _stored_session(self, session_token: str | None) -> Session:
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

    def _idle_lifetime(self, role: Role) -> timedelta:
        if self._legacy_idle_lifetime is not None:
            return self._legacy_idle_lifetime
        return timedelta(minutes=self.idle_policies.get().minutes_for(role))
