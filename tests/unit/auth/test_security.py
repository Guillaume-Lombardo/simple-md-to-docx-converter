"""Unit tests for deterministic password-work padding and token helpers."""

from __future__ import annotations

from datetime import UTC

import pytest
from argon2.exceptions import VerifyMismatchError
from pytest_mock import MockerFixture

from md_converter.auth.security import (
    Argon2idPasswordHasher,
    SecretsTokenGenerator,
    SystemClock,
    digest_token,
)


@pytest.mark.unit
def test_failed_verification_always_adds_current_profile_work(
    mocker: MockerFixture,
) -> None:
    backend = mocker.Mock()
    backend.hash.return_value = "current-dummy-hash"
    backend.verify.side_effect = [
        VerifyMismatchError("legacy mismatch"),
        VerifyMismatchError("dummy mismatch"),
    ]
    constructor = mocker.patch(
        "md_converter.auth.security.Argon2Hasher", return_value=backend
    )
    adapter = Argon2idPasswordHasher(memory_cost=19_456, time_cost=2, parallelism=1)

    assert adapter.verify_and_rehash("legacy-or-invalid-hash", "wrong") == (
        False,
        None,
    )
    constructor.assert_called_once()
    assert backend.verify.call_args_list == [
        mocker.call("legacy-or-invalid-hash", "wrong"),
        mocker.call("current-dummy-hash", "wrong"),
    ]


@pytest.mark.unit
def test_false_backend_result_is_padded_and_success_rehashes_only_when_needed(
    mocker: MockerFixture,
) -> None:
    backend = mocker.Mock()
    backend.hash.side_effect = ["current-dummy-hash", "replacement-hash"]
    backend.verify.side_effect = [False, False, True, True]
    backend.check_needs_rehash.side_effect = [False, True]
    mocker.patch("md_converter.auth.security.Argon2Hasher", return_value=backend)
    adapter = Argon2idPasswordHasher(memory_cost=19_456, time_cost=2, parallelism=1)

    assert adapter.verify_and_rehash("candidate", "wrong") == (False, None)
    assert adapter.verify_and_rehash("current", "correct") == (True, None)
    assert adapter.verify_and_rehash("legacy", "correct") == (
        True,
        "replacement-hash",
    )


@pytest.mark.unit
def test_token_digest_generator_and_clock_are_production_safe() -> None:
    token = SecretsTokenGenerator(16).generate()
    assert len(token) >= 22
    assert digest_token(token) != token
    assert SystemClock().now().tzinfo is UTC
