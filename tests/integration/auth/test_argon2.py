"""Integration tests against the real Argon2id implementation."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from md_converter.auth.security import Argon2idPasswordHasher, SecretsTokenGenerator


@pytest.mark.integration
def test_default_argon2id_parameters_verify_and_reject_passwords() -> None:
    adapter = Argon2idPasswordHasher(memory_cost=19_456, time_cost=2, parallelism=1)
    encoded = adapter.hash("correct-password")

    assert encoded.startswith("$argon2id$")
    assert "m=19456,t=2,p=1" in encoded
    assert adapter.verify_and_rehash(encoded, "correct-password") == (True, None)
    assert adapter.verify_and_rehash(encoded, "wrong-password") == (False, None)


@pytest.mark.integration
def test_obsolete_hash_is_replaced_only_after_successful_verification() -> None:
    old = PasswordHasher(memory_cost=8, time_cost=1, parallelism=1, type=Type.ID)
    legacy = old.hash("correct-password")
    current = Argon2idPasswordHasher(memory_cost=19_456, time_cost=2, parallelism=1)

    assert current.verify_and_rehash(legacy, "wrong-password") == (False, None)
    valid, replacement = current.verify_and_rehash(legacy, "correct-password")
    assert valid is True
    assert replacement is not None
    assert "m=19456,t=2,p=1" in replacement


@pytest.mark.integration
def test_session_tokens_have_at_least_128_bits_and_are_unique() -> None:
    generator = SecretsTokenGenerator(16)
    tokens = {generator.generate() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) >= 22 for token in tokens)
