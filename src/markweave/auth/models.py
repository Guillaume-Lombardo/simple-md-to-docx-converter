"""Authentication and authorization domain models."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

USERNAME_MAX_LENGTH = 255
SYSTEM_ACTOR_ID = UUID(int=0)
IDLE_SESSION_POLICY_ID = UUID(int=1)
DEFAULT_USER_IDLE_MINUTES = 30
DEFAULT_ADMIN_IDLE_MINUTES = 15
MINIMUM_IDLE_MINUTES = 5
MAXIMUM_USER_IDLE_MINUTES = 300
MAXIMUM_ADMIN_IDLE_MINUTES = 60


class Role(StrEnum):
    """Application-wide authorization roles."""

    USER = "user"
    ADMIN = "admin"


class AuthenticationAuditOperation(StrEnum):
    """Stable sensitive local-account mutation vocabulary."""

    BOOTSTRAP_ADMIN_CREATE = "bootstrap_admin_create"
    CREATE = "user_create"
    DEACTIVATE = "user_deactivate"
    REACTIVATE = "user_reactivate"
    RESET_PASSWORD = "user_password_reset"  # noqa: S105 - audit operation, not a secret
    REQUIRE_PASSWORD_CHANGE = "user_password_change_required"  # noqa: S105
    CHANGE_PASSWORD = "user_password_change"  # noqa: S105
    PROVISION_CREATE = "user_provision_create"
    PROVISION_UPDATE = "user_provision_update"


class IdleSessionPolicyOperation(StrEnum):
    """Stable system policy audit operation vocabulary."""

    UPDATE = "idle_session_policy_update"


def normalize_username(username: str) -> str:
    """Normalize usernames with NFKC, surrounding-space removal, and case folding."""
    return unicodedata.normalize("NFKC", username).strip().casefold()


@dataclass(slots=True)
class User:
    """A local account independent of its persistence adapter."""

    id: UUID
    username: str
    normalized_username: str
    password_hash: str
    role: Role
    active: bool = True
    auth_version: int = 0
    password_change_required: bool = False


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    """Validated startup account input with password material already hashed."""

    username: str
    normalized_username: str
    password_hash: str
    role: Role
    active: bool
    password_change_required: bool


@dataclass(frozen=True, slots=True)
class AuthenticationAuditContext:
    """Content-free audit context committed atomically with one account mutation."""

    id: UUID
    actor_id: UUID
    operation: AuthenticationAuditOperation
    created_at: datetime


@dataclass(slots=True)
class Session:
    """A revocable server-side session containing token digests only."""

    token_digest: str
    csrf_digest: str
    user_id: UUID
    auth_version: int
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Raw tokens returned once at successful login."""

    user: User
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class IdleSessionPolicy:
    """One system-wide pair of role-specific idle durations."""

    user_idle_minutes: int = DEFAULT_USER_IDLE_MINUTES
    admin_idle_minutes: int = DEFAULT_ADMIN_IDLE_MINUTES
    revision: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.user_idle_minutes, bool) or not (
            MINIMUM_IDLE_MINUTES <= self.user_idle_minutes <= MAXIMUM_USER_IDLE_MINUTES
        ):
            raise ValueError("Standard-user idle duration is invalid")
        if isinstance(self.admin_idle_minutes, bool) or not (
            MINIMUM_IDLE_MINUTES
            <= self.admin_idle_minutes
            <= MAXIMUM_ADMIN_IDLE_MINUTES
        ):
            raise ValueError("Administrator idle duration is invalid")
        if self.revision < 0:
            raise ValueError("Idle-session policy revision is invalid")

    def minutes_for(self, role: Role) -> int:
        """Resolve the duration for the user's current effective role."""
        return self.admin_idle_minutes if role is Role.ADMIN else self.user_idle_minutes


@dataclass(frozen=True, slots=True)
class IdleSessionPolicyAudit:
    """Immutable old/new policy evidence committed with one update."""

    id: UUID
    actor_id: UUID
    operation: IdleSessionPolicyOperation
    old_user_idle_minutes: int
    old_admin_idle_minutes: int
    new_user_idle_minutes: int
    new_admin_idle_minutes: int
    revision: int
    created_at: datetime
