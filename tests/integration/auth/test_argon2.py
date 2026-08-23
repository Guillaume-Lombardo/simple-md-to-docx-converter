"""Integration tests against the real Argon2id implementation."""

from __future__ import annotations

from statistics import median
from time import perf_counter_ns

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


@pytest.mark.integration
@pytest.mark.slow
def test_failed_authentication_medians_have_no_legacy_or_malformed_fast_path() -> None:
    current = Argon2idPasswordHasher(memory_cost=19_456, time_cost=2, parallelism=1)
    current_hash = current.hash("correct-password")
    legacy_hash = PasswordHasher(
        memory_cost=8, time_cost=1, parallelism=1, type=Type.ID
    ).hash("correct-password")
    candidates = {
        "current-wrong": current_hash,
        "unknown": current.dummy_hash,
        "inactive": current.dummy_hash,
        "legacy-wrong": legacy_hash,
        "malformed": "not-an-argon2-hash",
    }
    samples: dict[str, list[int]] = {name: [] for name in candidates}
    names = list(candidates)

    current.verify_and_rehash(current_hash, "warmup-wrong-password")
    for round_index in range(7):
        ordered = names[round_index % len(names) :] + names[: round_index % len(names)]
        for name in ordered:
            started = perf_counter_ns()
            assert current.verify_and_rehash(
                candidates[name], "deliberately-wrong-password"
            ) == (False, None)
            samples[name].append(perf_counter_ns() - started)

    medians = {name: median(values) for name, values in samples.items()}
    assert max(medians.values()) / min(medians.values()) <= 1.6, medians
