"""Unit tests for deterministic password-work padding and token helpers."""

from __future__ import annotations

from datetime import UTC

import pytest
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from markweave.auth.security import (
    Argon2idPasswordHasher,
    SecretsTokenGenerator,
    SystemClock,
    digest_token,
)

CURRENT_HASH = "current-hash-fixture"
FALSE_CURRENT_HASH = "false-current-hash-fixture"
DUMMY_CURRENT_HASH = "dummy-current-hash-fixture"
LEGACY_HASH = "legacy-hash-fixture"
MALFORMED_HASH = "malformed-hash-fixture"
MATCHING_VALUE = "matching-value"


class CountingArgon2Backend:
    """Structural Argon2 double that counts current and legacy work units."""

    def __init__(self) -> None:
        self.current_work_units = 0
        self.legacy_work_units = 0
        self.hash_calls = 0
        self._generated_hashes = 0

    def reset(self) -> None:
        self.current_work_units = 0
        self.legacy_work_units = 0
        self.hash_calls = 0

    def hash(self, password: str) -> str:
        del password
        self.current_work_units += 1
        self.hash_calls += 1
        self._generated_hashes += 1
        return (
            DUMMY_CURRENT_HASH
            if self._generated_hashes == 1
            else "replacement-current-hash-fixture"
        )

    def verify(self, password_hash: str, password: str) -> bool:
        if password_hash in {CURRENT_HASH, FALSE_CURRENT_HASH, DUMMY_CURRENT_HASH}:
            self.current_work_units += 1
        elif password_hash == LEGACY_HASH:
            self.legacy_work_units += 1
        else:
            raise VerifyMismatchError("malformed candidate")
        if password_hash == FALSE_CURRENT_HASH:
            return False
        if password == MATCHING_VALUE and password_hash in {CURRENT_HASH, LEGACY_HASH}:
            return True
        raise VerifyMismatchError("password mismatch")

    def check_needs_rehash(self, password_hash: str) -> bool:
        if password_hash in {CURRENT_HASH, FALSE_CURRENT_HASH, DUMMY_CURRENT_HASH}:
            return False
        if password_hash == LEGACY_HASH:
            return True
        raise InvalidHashError


def counting_adapter() -> tuple[Argon2idPasswordHasher, CountingArgon2Backend]:
    backend = CountingArgon2Backend()
    adapter = Argon2idPasswordHasher(
        memory_cost=19_456,
        time_cost=2,
        parallelism=1,
        backend=backend,
    )
    backend.reset()
    return adapter, backend


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "candidate"),
    [
        ("current-wrong", CURRENT_HASH),
        ("backend-false", FALSE_CURRENT_HASH),
        ("unknown", DUMMY_CURRENT_HASH),
        ("inactive", DUMMY_CURRENT_HASH),
        ("legacy-wrong", LEGACY_HASH),
        ("malformed", MALFORMED_HASH),
    ],
)
def test_every_failed_path_has_exactly_two_current_work_units(
    path: str, candidate: str
) -> None:
    adapter, backend = counting_adapter()

    assert adapter.verify_and_rehash(candidate, "wrong") == (False, None)
    assert backend.current_work_units == 2, path
    assert backend.legacy_work_units == (1 if candidate == LEGACY_HASH else 0)
    assert backend.hash_calls == 0


@pytest.mark.unit
def test_current_and_legacy_success_each_have_one_current_work_unit() -> None:
    current, current_backend = counting_adapter()
    assert current.verify_and_rehash(CURRENT_HASH, MATCHING_VALUE) == (True, None)
    assert current_backend.current_work_units == 1
    assert current_backend.hash_calls == 0

    legacy, legacy_backend = counting_adapter()
    assert legacy.verify_and_rehash(LEGACY_HASH, MATCHING_VALUE) == (
        True,
        "replacement-current-hash-fixture",
    )
    assert legacy_backend.current_work_units == 1
    assert legacy_backend.legacy_work_units == 1
    assert legacy_backend.hash_calls == 1


@pytest.mark.unit
def test_token_digest_generator_and_clock_are_production_safe() -> None:
    token = SecretsTokenGenerator(16).generate()
    assert len(token) >= 22
    assert digest_token(token) != token
    assert SystemClock().now().tzinfo is UTC
