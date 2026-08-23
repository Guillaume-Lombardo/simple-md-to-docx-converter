"""Concrete Argon2id, CSPRNG, digest, and clock adapters."""

from __future__ import annotations

import hashlib
import secrets
from contextlib import suppress
from datetime import UTC, datetime

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from argon2.low_level import Type


def digest_token(token: str) -> str:
    """Return a one-way digest suitable for server-side token lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Argon2idPasswordHasher:
    """Argon2id adapter with transparent successful-login rehashing."""

    def __init__(self, *, memory_cost: int, time_cost: int, parallelism: int) -> None:
        self._hasher = Argon2Hasher(
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
        try:
            valid = self._hasher.verify(password_hash, password)
        except VerificationError, VerifyMismatchError:
            self._pad_current_work(password)
            return False, None
        if not valid:
            self._pad_current_work(password)
            return False, None
        replacement = (
            self.hash(password)
            if self._hasher.check_needs_rehash(password_hash)
            else None
        )
        return True, replacement

    def _pad_current_work(self, password: str) -> None:
        """Perform one current-profile Argon2 verification on every failed path."""
        with suppress(VerificationError, VerifyMismatchError):
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
