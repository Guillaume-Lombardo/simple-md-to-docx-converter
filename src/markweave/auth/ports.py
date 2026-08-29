"""Persistence and security ports for authentication."""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from markweave.auth.models import (
        AuthenticationAuditContext,
        ProvisionedUser,
        Session,
        User,
    )


class UserRepository(Protocol):
    """Account persistence contract implemented by T06 memory and T12 profiles."""

    def bootstrap_admin(
        self, username: str, normalized_username: str, password_hash: str
    ) -> User: ...

    def create(
        self, user: User, *, audit: AuthenticationAuditContext | None = None
    ) -> None: ...

    def get_by_id(self, user_id: UUID) -> User | None: ...

    def get_by_normalized_username(self, normalized_username: str) -> User | None: ...

    def list(self) -> builtins.list[User]: ...

    def provision(
        self, records: builtins.list[ProvisionedUser], now: datetime
    ) -> builtins.list[User]:
        """Atomically create or replace a complete startup provisioning batch."""
        ...

    def commit_verified_login(
        self,
        user_id: UUID,
        expected_auth_version: int,
        replacement_hash: str | None,
    ) -> User | None:
        """Atomically validate account state/version and optionally replace its hash."""
        ...

    def commit_password_change(
        self,
        user_id: UUID,
        expected_auth_version: int,
        password_hash: str,
        audit: AuthenticationAuditContext,
    ) -> User | None:
        """Compare-and-set a required password renewal and its audit record."""
        ...

    def update_security(
        self,
        user_id: UUID,
        *,
        active: bool | None = None,
        password_hash: str | None = None,
        password_change_required: bool | None = None,
        audit: AuthenticationAuditContext | None = None,
    ) -> User | None:
        """Atomically mutate security state and increment its revocation version."""
        ...


class SessionRepository(Protocol):
    """Revocable session persistence contract."""

    def create(self, session: Session) -> None: ...

    def get(self, token_digest: str) -> Session | None: ...

    def save(self, session: Session) -> None: ...

    def revoke(self, token_digest: str) -> None: ...

    def revoke_user(self, user_id: UUID) -> None: ...


class PasswordHasher(Protocol):
    """Password hashing boundary."""

    @property
    def dummy_hash(self) -> str: ...

    def hash(self, password: str) -> str: ...

    def verify_and_rehash(
        self, password_hash: str, password: str
    ) -> tuple[bool, str | None]: ...


class TokenGenerator(Protocol):
    """Opaque token generation boundary."""

    def generate(self) -> str: ...


class Clock(Protocol):
    """Injectable UTC clock."""

    def now(self) -> datetime: ...


class ReadinessProbe(Protocol):
    """Cheap readiness boundary for the selected runtime profile."""

    def is_ready(self) -> bool: ...
