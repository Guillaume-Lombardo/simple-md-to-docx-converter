"""Concrete Argon2id, CSPRNG, digest, and clock adapters."""

from __future__ import annotations

import hashlib
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type


def digest_token(token: str) -> str:
    """Return a one-way digest suitable for server-side token lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Argon2Backend(Protocol):
    """Injectable subset of argon2-cffi used for structural work-count tests."""

    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...

    def check_needs_rehash(self, password_hash: str) -> bool: ...


class Argon2idPasswordHasher:
    """Argon2id adapter with transparent successful-login rehashing."""

    def __init__(
        self,
        *,
        memory_cost: int,
        time_cost: int,
        parallelism: int,
        backend: Argon2Backend | None = None,
    ) -> None:
        self._hasher = backend or Argon2Hasher(
            memory_cost=memory_cost,
            time_cost=time_cost,
            parallelism=parallelism,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    @property
    def dummy_hash(self) -> str:
        return self._dummy_hash

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify_and_rehash(
        self, password_hash: str, password: str
    ) -> tuple[bool, str | None]:
        current_profile = self._uses_current_profile(password_hash)
        try:
            valid = self._hasher.verify(password_hash, password)
        except InvalidHashError, VerificationError, VerifyMismatchError:
            self._pad_failed_verification(password, current_profile=current_profile)
            return False, None
        if not valid:
            self._pad_failed_verification(password, current_profile=current_profile)
            return False, None
        replacement = (
            self.hash(password)
            if self._hasher.check_needs_rehash(password_hash)
            else None
        )
        return True, replacement

    def _uses_current_profile(self, password_hash: str) -> bool:
        """Classify a fully parseable candidate without performing password work."""
        try:
            return not self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return False

    def _pad_failed_verification(self, password: str, *, current_profile: bool) -> None:
        """Complete every failed path to exactly two current-profile work units."""
        padding_units = 1 if current_profile else 2
        for _ in range(padding_units):
            with suppress(InvalidHashError, VerificationError, VerifyMismatchError):
                self._hasher.verify(self._dummy_hash, password)


class SecretsTokenGenerator:
    """Generate opaque CSPRNG tokens with configurable entropy."""

    def __init__(self, token_bytes: int) -> None:
        self._token_bytes = token_bytes

    def generate(self) -> str:
        return secrets.token_urlsafe(self._token_bytes)


class SystemClock:
    """UTC production clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)
