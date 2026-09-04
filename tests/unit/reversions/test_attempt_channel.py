"""Unit and security coverage for fixed-workspace attempt IPC."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

import markweave.reversions.attempt_channel as channel
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

pytestmark = pytest.mark.unit
LIMITS = channel.AttemptChannelLimits(max_input_bytes=16, max_output_bytes=32)
CONTENT_LIMITS = ReverseContentLimits(
    16,
    32,
    16,
    10,
    10,
    100,
    10,
    5,
    2,
    32,
    32,
    32,
    32,
)


def _request() -> ReverseAttemptRequest:
    return ReverseAttemptRequest(uuid4(), ".docm", CONTENT_LIMITS, b"source")


def _patch_workspace(mocker, workspace: Path) -> None:
    paths = {
        "REQUEST_METADATA_PATH": "request.json",
        "SOURCE_PATH": "source.bin",
        "RESULT_PATH": "result.bin",
        "RESPONSE_METADATA_PATH": "response.json",
        "_REQUEST_METADATA_TEMP_PATH": ".request.json.tmp",
        "_SOURCE_TEMP_PATH": ".source.bin.tmp",
        "_RESULT_TEMP_PATH": ".result.bin.tmp",
        "_RESPONSE_METADATA_TEMP_PATH": ".response.json.tmp",
    }
    for name, leaf in paths.items():
        mocker.patch.object(channel, name, workspace / leaf)


def test_request_metadata_round_trips_canonically_without_filename_or_policy() -> None:
    request = _request()
    encoded = channel.encode_request_metadata(request)

    assert encoded.endswith(b"\n")
    assert channel.decode_request_metadata(encoded, request.source) == request
    assert b"filename" not in encoded
    assert b"detected_format" not in encoded
    assert b"family" not in encoded
    assert b"parser_format" not in encoded
    assert b"argv" not in encoded
    assert b"mount" not in encoded
    assert b"network" not in encoded


def test_success_and_failure_metadata_round_trip_with_strict_result_shape() -> None:
    attempt_id = uuid4()
    success = ReverseAttemptSuccess(
        attempt_id, ReverseOutputMode.MARKDOWN_WITH_ASSETS, b"archive"
    )
    failure = ReverseAttemptFailure(attempt_id, ReverseErrorCategory.NEEDS_OCR)

    assert (
        channel.decode_response_metadata(
            channel.encode_response_metadata(success), success.result
        )
        == success
    )
    assert (
        channel.decode_response_metadata(
            channel.encode_response_metadata(failure), None
        )
        == failure
    )
    lease_failure = ReverseAttemptFailure(attempt_id, ReverseErrorCategory.LEASE_LOST)
    with pytest.raises(ReverseConversionError) as forbidden:
        channel.encode_response_metadata(lease_failure)
    assert forbidden.value.category is ReverseErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize(
    "mutate",
    (
        lambda encoded: encoded[:-1],
        lambda encoded: encoded + b" ",
        lambda encoded: b" " + encoded,
        lambda encoded: encoded.replace(b'"version":1', b'"version":2'),
        lambda encoded: encoded.replace(b'"version":1', b'"extra":true,"version":1'),
        lambda encoded: encoded.replace(
            b'"attempt_id":', b'"attempt_id":"duplicate","attempt_id":'
        ),
        lambda _encoded: b"{not-json}\n",
        lambda _encoded: b"{}\n",
        lambda _encoded: b"\xff\n",
    ),
)
def test_request_rejects_truncated_extra_noncanonical_and_unknown_metadata(
    mutate,
) -> None:
    request = _request()

    with pytest.raises(ReverseConversionError) as captured:
        channel.decode_request_metadata(
            mutate(channel.encode_request_metadata(request)), request.source
        )

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_request_rejects_forged_format_policy_and_empty_source() -> None:
    request = _request()
    metadata = json.loads(channel.encode_request_metadata(request))
    cases = [(channel.encode_request_metadata(request), b"")]
    for field, value in (
        ("detected_format", "docx"),
        ("family", "word"),
        ("parser_format", "docx"),
    ):
        forged = dict(metadata)
        forged[field] = value
        cases.append(
            (
                json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()
                + b"\n",
                request.source,
            )
        )

    for encoded, source in cases:
        with pytest.raises(ReverseConversionError) as captured:
            channel.decode_request_metadata(encoded, source)
        assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize(
    "attempt_id",
    (123, "not-a-uuid", "00000000-0000-0000-0000-00000000000A"),
)
def test_request_rejects_noncanonical_attempt_identity(attempt_id: object) -> None:
    request = _request()
    metadata = json.loads(channel.encode_request_metadata(request))
    metadata["attempt_id"] = attempt_id
    encoded = (
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )

    with pytest.raises(ReverseConversionError) as captured:
        channel.decode_request_metadata(encoded, request.source)

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("extra", 1),
        ("max_input_bytes", True),
        ("max_output_bytes", 0),
    ),
)
def test_request_rejects_extended_or_invalid_content_limits(
    mutation: str, value: object
) -> None:
    request = _request()
    metadata = json.loads(channel.encode_request_metadata(request))
    metadata["limits"][mutation] = value
    encoded = (
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )

    with pytest.raises(ReverseConversionError) as captured:
        channel.decode_request_metadata(encoded, request.source)

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_request_rejects_missing_content_limit() -> None:
    request = _request()
    metadata = json.loads(channel.encode_request_metadata(request))
    del metadata["limits"]["max_asset_count"]
    encoded = (
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )

    with pytest.raises(ReverseConversionError) as captured:
        channel.decode_request_metadata(encoded, request.source)

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_response_rejects_invalid_shapes_categories_and_modes() -> None:
    attempt_id = uuid4()
    success = ReverseAttemptSuccess(attempt_id, ReverseOutputMode.MARKDOWN, b"result")
    failure = ReverseAttemptFailure(attempt_id, ReverseErrorCategory.MALFORMED)
    success_metadata = json.loads(channel.encode_response_metadata(success))
    failure_metadata = json.loads(channel.encode_response_metadata(failure))
    invalid_cases = []
    for field, value, result in (
        ("mode", "unknown", b"result"),
        ("mode", 1, b"result"),
        ("mode", "markdown", None),
    ):
        changed = dict(success_metadata)
        changed[field] = value
        invalid_cases.append((changed, result))
    for field, value, result in (
        ("category", "unknown", None),
        ("category", 1, None),
        ("category", "lease_lost", None),
        ("category", "malformed", b"stale"),
    ):
        changed = dict(failure_metadata)
        changed[field] = value
        invalid_cases.append((changed, result))
    unknown = dict(success_metadata)
    unknown["type"] = "unknown"
    invalid_cases.append((unknown, None))

    for metadata, result in invalid_cases:
        encoded = (
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        with pytest.raises(ReverseConversionError) as captured:
            channel.decode_response_metadata(encoded, result)
        assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_fixed_workspace_request_and_success_round_trip_atomically(
    mocker, tmp_path: Path
) -> None:
    _patch_workspace(mocker, tmp_path)
    request = _request()
    response = ReverseAttemptSuccess(
        request.attempt_id, ReverseOutputMode.MARKDOWN, b"# result\n"
    )

    channel.write_request(request, LIMITS)
    assert channel.read_request(LIMITS) == request
    channel.write_response(response, LIMITS)
    assert channel.read_response(LIMITS, request.attempt_id) == response
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_fixed_workspace_failure_has_no_result_file(mocker, tmp_path: Path) -> None:
    _patch_workspace(mocker, tmp_path)
    failure = ReverseAttemptFailure(uuid4(), ReverseErrorCategory.ENCRYPTED)

    channel.write_response(failure, LIMITS)

    assert channel.read_response(LIMITS, failure.attempt_id) == failure
    assert not (tmp_path / "result.bin").exists()


def test_fixed_workspace_rejects_missing_nonregular_and_duplicate_files(
    mocker, tmp_path: Path
) -> None:
    _patch_workspace(mocker, tmp_path)
    with pytest.raises(ReverseConversionError) as missing:
        channel.read_request(LIMITS)
    assert missing.value.category is ReverseErrorCategory.PROTOCOL_ERROR

    (tmp_path / "request.json").mkdir()
    with pytest.raises(ReverseConversionError) as nonregular:
        channel.read_request(LIMITS)
    assert nonregular.value.category is ReverseErrorCategory.PROTOCOL_ERROR

    (tmp_path / "request.json").rmdir()
    (tmp_path / "source.bin").write_bytes(b"existing")
    with pytest.raises(ReverseConversionError) as duplicate:
        channel.write_request(_request(), LIMITS)
    assert duplicate.value.category is ReverseErrorCategory.PROTOCOL_ERROR

    (tmp_path / "result.bin").write_bytes(b"stale")
    with pytest.raises(ReverseConversionError) as failure_with_result:
        channel.write_response(
            ReverseAttemptFailure(uuid4(), ReverseErrorCategory.MALFORMED), LIMITS
        )
    assert failure_with_result.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_metadata_hard_limit_is_enforced_before_json_parsing() -> None:
    oversized = b"{" + b"x" * channel.MAX_METADATA_BYTES + b"}\n"
    with pytest.raises(ReverseConversionError) as captured:
        channel.decode_request_metadata(oversized, b"source")
    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_fixed_workspace_rejects_oversized_files_symlinks_and_stale_results(
    mocker, tmp_path: Path
) -> None:
    _patch_workspace(mocker, tmp_path)
    request = _request()
    channel.write_request(request, LIMITS)
    (tmp_path / "source.bin").write_bytes(b"x" * 17)
    with pytest.raises(ReverseConversionError) as oversized:
        channel.read_request(LIMITS)
    assert oversized.value.category is ReverseErrorCategory.RESOURCE_LIMIT

    (tmp_path / "source.bin").unlink()
    (tmp_path / "target").write_bytes(b"source")
    (tmp_path / "source.bin").symlink_to(tmp_path / "target")
    with pytest.raises(ReverseConversionError) as symlinked:
        channel.read_request(LIMITS)
    assert symlinked.value.category is ReverseErrorCategory.PROTOCOL_ERROR

    (tmp_path / "response.json").write_bytes(
        channel.encode_response_metadata(
            ReverseAttemptFailure(request.attempt_id, ReverseErrorCategory.MALFORMED)
        )
    )
    (tmp_path / "result.bin").write_bytes(b"stale")
    with pytest.raises(ReverseConversionError) as stale:
        channel.read_response(LIMITS, request.attempt_id)
    assert stale.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_response_is_bound_to_the_expected_attempt_identity(
    mocker, tmp_path: Path
) -> None:
    _patch_workspace(mocker, tmp_path)
    stale = ReverseAttemptSuccess(uuid4(), ReverseOutputMode.MARKDOWN, b"result")
    channel.write_response(stale, LIMITS)

    with pytest.raises(ReverseConversionError) as captured:
        channel.read_response(LIMITS, uuid4())

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_configured_byte_limits_apply_before_writing_payloads(
    mocker, tmp_path: Path
) -> None:
    _patch_workspace(mocker, tmp_path)
    request = ReverseAttemptRequest(
        uuid4(),
        ".pdf",
        replace(CONTENT_LIMITS, max_input_bytes=17),
        b"x" * 17,
    )
    success = ReverseAttemptSuccess(
        request.attempt_id, ReverseOutputMode.MARKDOWN, b"x" * 33
    )

    for operation in (
        lambda: channel.write_request(request, LIMITS),
        lambda: channel.write_response(success, LIMITS),
    ):
        with pytest.raises(ReverseConversionError) as captured:
            operation()
        assert captured.value.category is ReverseErrorCategory.RESOURCE_LIMIT
    assert not tuple(tmp_path.iterdir())


def test_transport_ceiling_rejects_larger_embedded_input_or_output_policy(
    mocker, tmp_path: Path
) -> None:
    _patch_workspace(mocker, tmp_path)
    request = ReverseAttemptRequest(
        uuid4(),
        ".pdf",
        replace(
            CONTENT_LIMITS,
            max_input_bytes=17,
            max_output_bytes=33,
            max_package_bytes=33,
        ),
        b"source",
    )
    (tmp_path / "request.json").write_bytes(channel.encode_request_metadata(request))
    (tmp_path / "source.bin").write_bytes(request.source)

    with pytest.raises(ReverseConversionError) as captured:
        channel.read_request(LIMITS)

    assert captured.value.category is ReverseErrorCategory.RESOURCE_LIMIT


@pytest.mark.parametrize("invalid", ((0, 1), (1, 0), (True, 1), (1, False)))
def test_channel_limits_require_positive_non_boolean_integers(
    invalid: tuple[int, int],
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        channel.AttemptChannelLimits(*invalid)
