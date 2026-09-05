"""Unit tests for the fixed reverse-attempt child entrypoint."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, cast
from uuid import UUID

import anydoc
import pytest

from markweave.reversions import attempt_main
from markweave.reversions._anydoc_compat import ParsedSource, RenderedDocument
from markweave.reversions.assets import (
    AssetNormalizationResult,
    NormalizedAsset,
    NormalizedAssetReference,
)
from markweave.reversions.attempt_channel import AttemptChannelLimits
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.formats import FormatAdmission, FormatFamily
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

pytestmark = pytest.mark.unit

ATTEMPT_ID = UUID("11111111-1111-4111-8111-111111111111")
LIMITS = ReverseContentLimits(
    max_input_bytes=10_000,
    max_output_bytes=20_000,
    max_image_source_bytes=5_000,
    max_image_width_pixels=100,
    max_image_height_pixels=100,
    max_image_pixels=10_000,
    max_svg_elements=100,
    max_svg_depth=10,
    max_asset_count=10,
    max_total_asset_source_bytes=8_000,
    max_total_asset_output_bytes=8_000,
    max_markdown_bytes=10_000,
    max_package_bytes=20_000,
)


def _request(extension: str = ".docx") -> ReverseAttemptRequest:
    return ReverseAttemptRequest(ATTEMPT_ID, extension, LIMITS, b"source")


def test_channel_limits_require_canonical_positive_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKWEAVE_REVERSE_MAX_INPUT_BYTES", "10000")
    monkeypatch.setenv("MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES", "20000")

    assert attempt_main.channel_limits_from_environment() == AttemptChannelLimits(
        10_000, 20_000
    )


@pytest.mark.parametrize("value", ["", "0", "01", "-1", " 1", "1.0", "x"])
def test_channel_limits_reject_noncanonical_environment(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MARKWEAVE_REVERSE_MAX_INPUT_BYTES", value)
    monkeypatch.setenv("MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES", "20000")

    with pytest.raises(ValueError, match="configuration is invalid"):
        attempt_main.channel_limits_from_environment()


def test_pdf_conversion_returns_plain_markdown(mocker: Any) -> None:
    mocker.patch.object(
        attempt_main,
        "parse_source",
        return_value=ParsedSource(
            FormatAdmission(FormatFamily.PDF, ".pdf", "pdf", "pdf"),
            None,
            "PDF text\n",
        ),
    )

    response = attempt_main.convert_request(_request(".pdf"))

    assert response == ReverseAttemptSuccess(
        ATTEMPT_ID, ReverseOutputMode.MARKDOWN, b"PDF text\n"
    )


def test_document_conversion_normalizes_and_injects_assets(mocker: Any) -> None:
    document = cast(anydoc.Document, object())
    parse = mocker.patch.object(
        attempt_main,
        "parse_source",
        return_value=ParsedSource(
            FormatAdmission(FormatFamily.WORD, ".docx", "docx", "docx"),
            document,
            None,
        ),
    )
    sources = (object(),)
    mocker.patch.object(attempt_main, "extract_asset_sources", return_value=sources)
    path = PurePosixPath("assets/image-0001.png")
    normalized = AssetNormalizationResult(
        (NormalizedAssetReference("anydoc:0", path),),
        (NormalizedAsset(path, b"normalized"),),
        0,
    )
    normalize = mocker.patch.object(
        attempt_main, "normalize_assets", return_value=normalized
    )
    render = mocker.patch.object(
        attempt_main,
        "render_document_result",
        return_value=RenderedDocument("![](assets/image-0001.png)\n", (0,)),
    )

    response = attempt_main.convert_request(_request())

    assert response.mode is ReverseOutputMode.MARKDOWN_WITH_ASSETS
    assert response.result.startswith(b"PK")
    parse.assert_called_once_with(b"source", ".docx")
    normalize.assert_called_once_with(sources, LIMITS.asset_limits)
    render.assert_called_once_with(document, (path,))


def test_document_with_only_unavailable_images_returns_closed_zip(mocker: Any) -> None:
    document = cast(anydoc.Document, object())
    mocker.patch.object(
        attempt_main,
        "parse_source",
        return_value=ParsedSource(
            FormatAdmission(FormatFamily.WORD, ".docx", "docx", "docx"),
            document,
            None,
        ),
    )
    mocker.patch.object(attempt_main, "extract_asset_sources", return_value=(object(),))
    mocker.patch.object(
        attempt_main,
        "normalize_assets",
        return_value=AssetNormalizationResult(
            (NormalizedAssetReference("anydoc:unavailable:0", None),), (), 1
        ),
    )
    mocker.patch.object(
        attempt_main,
        "render_document_result",
        return_value=RenderedDocument("Unavailable\n", (0,)),
    )

    response = attempt_main.convert_request(_request())

    assert response.mode is ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS
    assert response.result.startswith(b"PK")


def test_discarded_render_occurrences_remove_orphan_assets() -> None:
    first_path = PurePosixPath("assets/image-0001.png")
    discarded_path = PurePosixPath("assets/image-0002.png")
    normalized = AssetNormalizationResult(
        (
            NormalizedAssetReference("anydoc:0", first_path),
            NormalizedAssetReference("anydoc:1", discarded_path),
            NormalizedAssetReference("anydoc:unavailable:2", None),
        ),
        (
            NormalizedAsset(first_path, b"first"),
            NormalizedAsset(discarded_path, b"discarded"),
        ),
        1,
    )

    retained = attempt_main._retain_rendered_assets(normalized, (0, 2))

    assert retained == AssetNormalizationResult(
        (
            NormalizedAssetReference("anydoc:0", first_path),
            NormalizedAssetReference("anydoc:unavailable:2", None),
        ),
        (NormalizedAsset(first_path, b"first"),),
        1,
    )


@pytest.mark.parametrize("indices", [(1, 0), (0, 0), (-1,), (3,), (True,)])
def test_invalid_retained_occurrences_fail_closed(indices: tuple[int, ...]) -> None:
    normalized = AssetNormalizationResult(
        (NormalizedAssetReference("anydoc:0", None),) * 3,
        (),
        3,
    )

    with pytest.raises(RuntimeError, match="Invalid internal"):
        attempt_main._retain_rendered_assets(normalized, indices)


def test_invalid_internal_parse_state_fails_closed(mocker: Any) -> None:
    parsed = mocker.Mock(
        admission=FormatAdmission(FormatFamily.PDF, ".pdf", "pdf", "pdf"),
        document=None,
        markdown=None,
    )
    mocker.patch.object(attempt_main, "parse_source", return_value=parsed)

    with pytest.raises(RuntimeError, match="Invalid internal"):
        attempt_main.convert_request(_request(".pdf"))


def test_expected_and_unexpected_failures_are_content_free(mocker: Any) -> None:
    request = _request()
    mocker.patch.object(
        attempt_main,
        "convert_request",
        side_effect=lambda _request: reject(ReverseErrorCategory.ENCRYPTED),
    )
    assert attempt_main._execute(request) == ReverseAttemptFailure(
        ATTEMPT_ID, ReverseErrorCategory.ENCRYPTED
    )

    mocker.patch.object(
        attempt_main, "convert_request", side_effect=RuntimeError("private path")
    )
    assert attempt_main._execute(request) == ReverseAttemptFailure(
        ATTEMPT_ID, ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_main_reads_executes_and_writes_without_output(mocker: Any) -> None:
    channel_limits = AttemptChannelLimits(10_000, 20_000)
    request = _request()
    response = ReverseAttemptSuccess(ATTEMPT_ID, ReverseOutputMode.MARKDOWN, b"ok")
    mocker.patch.object(
        attempt_main, "channel_limits_from_environment", return_value=channel_limits
    )
    mocker.patch.object(attempt_main, "wait_for_request", return_value=request)
    mocker.patch.object(attempt_main, "_execute", return_value=response)
    write = mocker.patch.object(attempt_main, "write_response")
    mocker.patch.object(attempt_main, "_linger_until_terminated", return_value=0)
    mocker.patch.object(attempt_main.sys, "argv", ["attempt_main"])

    assert attempt_main.main() == 0
    write.assert_called_once_with(response, channel_limits)


def test_main_rejects_arguments_and_unreadable_request(mocker: Any) -> None:
    mocker.patch.object(attempt_main.sys, "argv", ["attempt_main", "override"])
    assert attempt_main.main() == 2

    mocker.patch.object(attempt_main.sys, "argv", ["attempt_main"])
    mocker.patch.object(
        attempt_main,
        "channel_limits_from_environment",
        return_value=AttemptChannelLimits(1, 1),
    )
    mocker.patch.object(
        attempt_main,
        "wait_for_request",
        side_effect=lambda _limits: reject(ReverseErrorCategory.PROTOCOL_ERROR),
    )
    assert attempt_main.main() == 2


def test_main_fails_closed_when_response_cannot_be_written(mocker: Any) -> None:
    channel_limits = AttemptChannelLimits(10_000, 20_000)
    request = _request()
    mocker.patch.object(attempt_main.sys, "argv", ["attempt_main"])
    mocker.patch.object(
        attempt_main, "channel_limits_from_environment", return_value=channel_limits
    )
    mocker.patch.object(attempt_main, "wait_for_request", return_value=request)
    mocker.patch.object(
        attempt_main,
        "_execute",
        return_value=ReverseAttemptFailure(
            ATTEMPT_ID, ReverseErrorCategory.PROTOCOL_ERROR
        ),
    )
    mocker.patch.object(
        attempt_main,
        "write_response",
        side_effect=lambda *_args: reject(ReverseErrorCategory.PROTOCOL_ERROR),
    )

    assert attempt_main.main() == 3
