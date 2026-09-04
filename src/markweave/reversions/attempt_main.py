"""Fixed entrypoint for one credentialless reverse-conversion attempt."""

from __future__ import annotations

import os
import sys
from typing import cast

from markweave.reversions._anydoc_compat import (
    extract_asset_sources,
    parse_source,
    render_document,
)
from markweave.reversions.assets import normalize_assets
from markweave.reversions.attempt_channel import (
    AttemptChannelLimits,
    read_request,
    write_response,
)
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.manifest import DetectedFormat, ManifestSource
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptResponse,
    ReverseAttemptSuccess,
    ReverseOutputMode,
)
from markweave.reversions.package import build_reverse_package

_MAX_INPUT_ENV = "MARKWEAVE_REVERSE_MAX_INPUT_BYTES"
_MAX_OUTPUT_ENV = "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES"
_MAX_ENV_DIGITS = 20


def _required_positive_integer(name: str) -> int:
    value = os.environ.get(name)
    if (
        value is None
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > _MAX_ENV_DIGITS
    ):
        raise ValueError("Reverse-attempt channel configuration is invalid")
    return int(value)


def channel_limits_from_environment() -> AttemptChannelLimits:
    """Load the broker-owned transport envelope without inventing defaults."""

    return AttemptChannelLimits(
        max_input_bytes=_required_positive_integer(_MAX_INPUT_ENV),
        max_output_bytes=_required_positive_integer(_MAX_OUTPUT_ENV),
    )


def convert_request(request: ReverseAttemptRequest) -> ReverseAttemptSuccess:
    """Convert one admitted source into an unpublished bounded result."""

    parsed = parse_source(request.source, request.extension)
    normalized_assets = ()
    asset_references = ()
    unavailable_asset_count = 0
    if parsed.document is None:
        if parsed.markdown is None:
            raise RuntimeError("Invalid internal reverse-conversion state")
        markdown = parsed.markdown
    else:
        sources = extract_asset_sources(parsed.document)
        normalized = normalize_assets(sources, request.limits.asset_limits)
        markdown = render_document(
            parsed.document,
            tuple(reference.path for reference in normalized.references),
        )
        normalized_assets = normalized.assets
        asset_references = tuple(reference.path for reference in normalized.references)
        unavailable_asset_count = normalized.unavailable_asset_count

    package = build_reverse_package(
        markdown,
        normalized_assets,
        asset_references,
        unavailable_asset_count=unavailable_asset_count,
        source=ManifestSource(
            parsed.admission.family.value,
            cast(DetectedFormat, parsed.admission.parser_format),
        ),
        limits=request.limits.package_limits,
    )
    if package.extension == ".md":
        mode = ReverseOutputMode.MARKDOWN
    elif normalized_assets:
        mode = ReverseOutputMode.MARKDOWN_WITH_ASSETS
    else:
        mode = ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS
    return ReverseAttemptSuccess(request.attempt_id, mode, package.content)


def _execute(request: ReverseAttemptRequest) -> ReverseAttemptResponse:
    try:
        return convert_request(request)
    except ReverseConversionError as error:
        return ReverseAttemptFailure(request.attempt_id, error.category)
    # This process boundary must convert every private failure into fixed metadata.
    except Exception:
        return ReverseAttemptFailure(
            request.attempt_id, ReverseErrorCategory.PROTOCOL_ERROR
        )


def main() -> int:
    """Run exactly one fixed-workspace attempt without writing document data to logs."""

    if len(sys.argv) != 1:
        return 2
    try:
        channel_limits = channel_limits_from_environment()
        request = read_request(channel_limits)
    except ReverseConversionError, ValueError:
        return 2
    response = _execute(request)
    try:
        write_response(response, channel_limits)
    except ReverseConversionError:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the image smoke
    raise SystemExit(main())
