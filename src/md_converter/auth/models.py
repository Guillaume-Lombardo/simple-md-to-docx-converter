"""Authentication and authorization domain models."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


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
