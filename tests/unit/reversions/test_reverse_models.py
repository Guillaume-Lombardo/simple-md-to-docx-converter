"""Unit coverage for isolated reverse-attempt models."""

from typing import cast
from uuid import UUID, uuid4

import pytest

from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

pytestmark = pytest.mark.unit


def _limits() -> ReverseContentLimits:
    return ReverseContentLimits(
        1_000,
        2_000,
        500,
        100,
        100,
        10_000,
        100,
        16,
        8,
        4_000,
        1_500,
        1_000,
        2_000,
    )


def test_attempt_models_carry_only_opaque_identity_format_and_bounded_data() -> None:
    attempt_id = uuid4()
    limits = _limits()

    request = ReverseAttemptRequest(attempt_id, ".DOCX", limits, b"source")
    success = ReverseAttemptSuccess(attempt_id, ReverseOutputMode.MARKDOWN, b"result")
    failure = ReverseAttemptFailure(attempt_id, ReverseErrorCategory.MALFORMED)

    assert request.source == b"source"
    assert request.extension == ".docx"
    assert request.limits.image_limits.max_pixels == 10_000
    assert request.limits.asset_limits.max_asset_count == 8
    assert request.limits.package_limits.max_package_bytes == 2_000
    assert success.result == b"result"
    assert failure.category is ReverseErrorCategory.MALFORMED


def test_attempt_request_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ReverseAttemptRequest(uuid4(), ".pdf", _limits(), b"")


def test_attempt_models_reject_runtime_type_confusion() -> None:
    attempt_id = uuid4()
    with pytest.raises(ValueError, match="identity"):
        ReverseAttemptRequest(cast(UUID, str(attempt_id)), ".pdf", _limits(), b"source")
    with pytest.raises(ValueError, match="result"):
        ReverseAttemptSuccess(
            attempt_id, cast(ReverseOutputMode, "markdown"), b"result"
        )
    with pytest.raises(ValueError, match="category"):
        ReverseAttemptFailure(attempt_id, cast(ReverseErrorCategory, "malformed"))
    with pytest.raises(ValueError, match="identity"):
        ReverseAttemptSuccess(
            cast(UUID, str(attempt_id)), ReverseOutputMode.MARKDOWN, b"result"
        )
    with pytest.raises(ValueError, match="identity"):
        ReverseAttemptFailure(
            cast(UUID, str(attempt_id)), ReverseErrorCategory.MALFORMED
        )


def test_attempt_request_enforces_embedded_input_limit_and_limit_types() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        ReverseAttemptRequest(uuid4(), ".pdf", _limits(), b"x" * 1_001)
    with pytest.raises(ValueError, match="limits"):
        ReverseAttemptRequest(
            uuid4(), ".pdf", cast(ReverseContentLimits, "invalid"), b"source"
        )


@pytest.mark.parametrize("field", range(13))
def test_content_limits_require_positive_integers(field: int) -> None:
    values = [
        1_000,
        2_000,
        500,
        100,
        100,
        10_000,
        100,
        16,
        8,
        4_000,
        4_000,
        1_000,
        2_000,
    ]
    values[field] = 0
    with pytest.raises(ValueError):
        ReverseContentLimits(*values)


def test_content_result_limits_must_fit_output_envelope() -> None:
    with pytest.raises(ValueError, match="fit the output"):
        ReverseContentLimits(1_000, 100, 50, 10, 10, 100, 10, 5, 1, 50, 50, 101, 100)


@pytest.mark.parametrize(
    "values",
    [
        (1_000, 2_000, 500, 100, 100, 10_000, 100, 16, 8, 4_000, 2_001, 1_000, 2_000),
        (1_000, 2_000, 500, 100, 100, 10_000, 100, 16, 8, 4_000, 2_000, 1_001, 1_000),
    ],
)
def test_content_asset_and_markdown_limits_fit_package_envelope(
    values: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="fit the output"):
        ReverseContentLimits(*values)
